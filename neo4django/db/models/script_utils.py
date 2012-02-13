import neo4jrestclient.client as neo4j
from .. import DEFAULT_DB_ALIAS, connections

class LazyBase(object):
    """
    A mixin to make elements of the REST client lazy.
    """
    def __init__(self, url, dic):
        self._dont_update = True
        super(LazyBase, self).__init__(url, create=False)
        self._dic = dic.copy()
        self._dont_update = False

    def update(self, *args, **kwargs):
        if not self._dont_update:
            super(LazyBase, self).update(*args, **kwargs)

    @classmethod
    def from_dict(cls, dic):
        return cls(dic['self'], dic)

class LazyNode(LazyBase, neo4j.Node):
    id_url_template = 'node/%d'

class LazyRelationship(LazyBase, neo4j.Relationship):
    id_url_template = 'relationship/%d'

    def __init__(self, *args, **kwargs):
        self._custom_lookup = None
        super(LazyRelationship, self).__init__(*args, **kwargs)

    def set_custom_node_lookup(self, lookup):
        """
        Specify a dict-like lookup object from which nodes can be pulled. Keys
        should be node urls.
        """
        #HACK solution for a lazy sub-graph
        self._custom_lookup = lookup

    @property
    def start(self):
        key = self._dic['start']
        if self._custom_lookup is not None and key:
            try:
                return self._custom_lookup[id_from_url(key)]
            except KeyError:
                pass
        return super(LazyRelationship, self).start

    @property
    def end(self):
        key = self._dic['end']
        if self._custom_lookup is not None and key:
            try:
                return self._custom_lookup[id_from_url(key)]
            except KeyError:
                pass
        return super(LazyRelationship, self).end

def batch_base(ids, cls, using):
    """
    A function to replace the REST client's non-lazy batching.
    """
    #HACK to get around REST client limitations
    gremlin_func = 'e' if issubclass(cls, neo4j.Relationship) else 'v'
    script = \
    """
    t = new Table()
    for (def id : ids) {
        g.%s(id).as('elements').table(t,['elements']).iterate()
    }
    results = t
    """
    script %= gremlin_func
    result_table = connections[using].gremlin(script, ids=ids)
    return [cls.from_dict(v[0]) for v in result_table['data']]
    
def batch_rels(ids, using):
    return batch_base(ids, LazyRelationship, using)

def batch_nodes(ids, using):
    return batch_base(ids, LazyNode, using)

def batch_paths(paths, using):
    """
    A function to replace the REST client's non-lazy batching of paths.
    """
    #TODO untested
    batched = []

    tx = connections[using].transaction(using_globals=False)
    for p in paths:
        for n_url in p['nodes']:
            tx.subscribe('GET',n_url)
        for r_url in p['relationships']:
            tx.subscribe('GET', r_url)
    result_dict = tx._batch()
    rel_by_url = {}
    node_by_url = {}
    for v in result_dict.values():
        d = v['body']
        if "start" in d:
            rel_by_url[d['self']] = LazyRelationship.from_dict(d)
        else:
            node_by_url[d['self']] = LazyNode.from_dict(d)
    for p in paths:
        node_it = (node_by_url[n_url] for n_url in iter(p['nodes']))
        rel_it = (rel_by_url[r_url] for r_url in iter(p['relationships']))
        p_list = []
        while True:
            try:
                p_list.append(node_it.next())
                p_list.append(rel_it.next())
            except StopIteration:
                break
        batched.append(tuple(p_list))
    return batched

def query_indices(name_and_query, using):
    """
    Takes a list of index name/query pairs and returns the resulting nodes.
    """
    #send in an ordered set of index names and query pairs
    #TODO this will change when we attempt #35, since this assumes intersection
    #type_name = self.nodetype._type_name()
    #return_expr = reduce(and_,
    #                     (js_expression_from_condition(c, J('testedNode')) 
    #                      for c in unindexed))
    result_set = connections[using].gremlin_tx('results = Neo4Django.queryNodeIndices(queries)', queries=name_and_query)
    
    #make the result_set not insane (properly lazy)
    return [LazyNode.from_dict(dic) for dic in result_set._list] if result_set else []

def id_from_url(url):
    from urlparse import urlsplit
    from posixpath import dirname, basename
    path = urlsplit(url).path
    b = basename(path)
    return int(b if b else dirname(path))

class GremlinSnippet(object):
    def __init__(self, name, script, in_args=['results'], out_args=['results']):
        self.script = script
        self.in_args = in_args
        self.out_arg = out_arg
