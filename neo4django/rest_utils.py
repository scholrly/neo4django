from operator import itemgetter
from itertools import izip_longest, chain, ifilter

def id_from_url(url):
    from urlparse import urlsplit
    from posixpath import dirname, basename
    path = urlsplit(url).path
    b = basename(path)
    return int(b if b else dirname(path))

class Neo4jTable(object):
    def __init__(self, d):
        self.data = d['data']
        self.column_names = d['columns']

    def get_rows(self, column_name_pred):
        """
        Return all table columns that match the provided predicate.

        If the argument is a string, test for equality instead. If the argument
        is None, the identity function is assumed.
        """
        if column_name_pred is None:
            column_name_pred = lambda s:s
        elif not callable(column_name_pred):
            column_name = column_name_pred
            column_name_pred = lambda s: s == column_name
        columns = [i for i,c in enumerate(self.column_names) if column_name_pred(c)]
        col_getter = itemgetter(*columns)
        return chain.from_iterable(rc if isinstance(rc, tuple) else (rc,)
                                 for rc in (col_getter(r)
                                            for r in self.data))

def prettify_path(path_dict):
    nodes = ['(%d)' % id_from_url(url) for url in path_dict['nodes']]
    rels = ['[%d]' % id_from_url(url) for url in path_dict['relationships']]
    return '->'.join(ifilter(None, chain.from_iterable(izip_longest(nodes, rels))))
