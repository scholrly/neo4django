from urlparse import urlparse
from neo4jrestclient.client import GraphDatabase, RAW as RETURNS_RAW
from neo4jrestclient.request import Request
from django.conf import settings as _settings
from django.core import exceptions

from pkg_resources import resource_stream as _pkg_resource_stream
from collections import namedtuple
import re as _re
import warnings

from .exceptions import GremlinLibraryCouldNotBeLoaded as LibraryCouldNotLoad
from .rest_utils import Neo4jTable

#TODO move this somewhere sane (settings?)
LIBRARY_LOADING_RETRIES = 1

#TODO DRY considerations
LIBRARY_NAME = 'Neo4Django'
#TODO issue #128 - better gremlin error passing
LIBRARY_LOADING_ERROR = 'neo4django: "%s" library not loaded!'
LIBRARY_ERROR_REGEX = _re.compile(LIBRARY_LOADING_ERROR % '.*?')

other_libraries = {}

class EnhancedGraphDatabase(GraphDatabase):
    def __init__(self, *args, **kwargs):
        cleandb_uri = kwargs.pop('CLEANDB_URI', None)
        super(EnhancedGraphDatabase, self).__init__(*args, **kwargs)
        if cleandb_uri:
            parsed_url = urlparse(self.url)
            cleandb_uri = "%s://%s%s" % (parsed_url.scheme,
                                         parsed_url.netloc, cleandb_uri)

            self._cleandb_uri = cleandb_uri

    def new_request(self):
        # Newer versions of neo4jrestclient support auth more robustly
        # the older versions do not support at all, so we have to check here
        try:
            auth = self._auth
        except AttributeError:
            auth = {}
        return Request(**auth)

    def cleandb(self):
        request = self.new_request()
        response, content = request.delete(self._cleandb_uri)
        if response.status != 200:
            warnings.warn('The CLEANDB_URI you specified is invalid: %s' \
                          % self._cleandb_uri)
            # then try to do it with gremlin
            script = """
            try
            {
                indexManager = g.getRawGraph().index()
                indexManager.nodeIndexNames().each{g.dropIndex(it)}
                indexManager.relationshipIndexNames().each{g.dropIndex(it)}
                g.V.filter{it.id!=0}.sideEffect{g.removeVertex(it)}.iterate();
                results = true
            }
            catch(Exception e){results = false}
            """
            gremlin_ret = self.gremlin(script, raw=True)
            if gremlin_ret != 'true':
                error_msg = "\nDatabase couldn't be cleared - have you installed the cleandb extension at https://github.com/jexp/neo4j-clean-remote-db-addon?"
                raise exceptions.ImproperlyConfigured(error_msg)

    def gremlin(self, script, tx=False, raw=False, **params):
        """
        Execute a Gremlin script server-side and return the results.
        Transactions will be automatically managed, unless otherwise requested
        in the script, or the tx argument is set to True- in which case the
        whole script will be wrapped in a transaction.
        """
        #import statements have to be at the top, so this global try won't
        #do without pulling them up- luckily imports aren't super complicated
        #in the Groovy grammar. that said...
        #XXX this would be an easy place for a bug, and an actual parser
        #would be better...
        import_regex = _re.compile('\w*import [^{}]*?\w*(;|$)', _re.MULTILINE)
        import_statements = [m.group() for m in import_regex.finditer(script)]
        importless_script = import_regex.sub('', script)

        lib_script = '''
        import groovy.json.JsonBuilder;
        %(imports)s
        %(tx_begin)s
        try{
        %(main_code)s
        } catch (MissingPropertyException mpe) {
            %(tx_fail)s
            if (mpe.property in %(library_names)s) {
                results =String.format('%(load_error)s', mpe.property)
            }
            else { throw mpe }
        }
        catch (Exception otherE) {
            %(tx_fail)s
            throw otherE
        }
        %(tx_end)s
        if (results instanceof Map) {
            results = new JsonBuilder(results).toString()
        }
        results
        '''
        library_names = ("'%s'" % str(c) for c in 
                         (['Neo4Django'] + other_libraries.keys()))
        library_list = '[' + ','.join(library_names) + ']'
        repl_dict = {'imports':('\n'.join(s.strip(';') for s in import_statements)),
                     'tx_begin':'',
                     'main_code':importless_script,
                     'tx_fail':'',
                     'library_names':library_list,
                     'load_error':LIBRARY_LOADING_ERROR,
                     'tx_end':''
                    }
        if tx:
            repl_dict['tx_begin'] = 'g.setMaxBufferSize(0); rootTx = g.getRawGraph().beginTx()'
            repl_dict['tx_end'] = 'rootTx.success(); rootTx.finish();' \
                                  'g.setMaxBufferSize(1)'
            repl_dict['tx_fail'] = 'rootTx.failure(); rootTx.finish();' \
                                  'g.setMaxBufferSize(1)'
        lib_script %= repl_dict
        ext = self.extensions.GremlinPlugin

        def include_main_library(s):
            #get the library source
            lib_source = _pkg_resource_stream(__package__.split('.',1)[0],
                                        'gremlin/library.groovy').read()
            return lib_source + '\n' + s

        def include_unloaded_libraries(s):
            for name in other_libraries.keys():
                if not other_libraries[name].loaded:
                    source = other_libraries[name].source
                    s = source + '\n' +  s
            return s

        def include_all_libraries(s):
            for name in other_libraries.keys():
                source = other_libraries[name].source
                other_libraries[name] = Library(source, True)
                s = source + '\n' +  s
            return include_main_library(s)

        def send_script(s, params):
            execute_kwargs = {}
            if raw:
                execute_kwargs['returns'] = RETURNS_RAW
            script_rv = ext.execute_script(s, params=params, **execute_kwargs)
            if isinstance(script_rv, basestring):
                if LIBRARY_ERROR_REGEX.match(script_rv):
                    raise LibraryCouldNotLoad
                elif script_rv.startswith('{'):
                    import json
                    return json.loads(script_rv)
            return script_rv

        if getattr(_settings, 'NEO4DJANGO_DEBUG_GREMLIN', False):
            all_libs = include_all_libraries(lib_script)
            return send_script(all_libs, params)
        for i in xrange(LIBRARY_LOADING_RETRIES + 1):
            try:
                return send_script(include_unloaded_libraries(lib_script), 
                                   params)
            except LibraryCouldNotLoad:
                if i == 0:
                    lib_script = include_all_libraries(lib_script)
        raise LibraryCouldNotLoad

    def gremlin_tx(self, script, **params):
        """
        Execute a Gremlin script server-side and return the results. The script
        will be wrapped in a transaction.
        """
        return self.gremlin(script, tx=True, **params)

    def cypher(self, query, **params):
        ext = self.extensions.CypherPlugin
        return Neo4jTable(ext.execute_query(query=query, params=params))

Library = namedtuple('Library', ['source', 'loaded'])

def load_library(library_class, library_source):
    if not isinstance(library_class, basestring):
        raise TypeError('Expected a string class name, not %s.' 
                        % str(library_class))
    if library_class == LIBRARY_NAME:
        raise ValueError('%s is a reserved library name.' % LIBRARY_NAME)
    other_libraries[library_class] = Library(library_source, False)

def remove_library(library_class):
    other_libraries.pop(library_class, None)
