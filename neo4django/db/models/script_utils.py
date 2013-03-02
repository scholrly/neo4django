import neo4jrestclient.client as neo4j
from .. import DEFAULT_DB_ALIAS, connections
from ...rest_utils import id_from_url

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

class SkeletonBase(object):
    """
    A mixin to allow REST element construction from an id and property dict.

    Expects a LazyNode or LazyRelationship in the class hierarchy.
    """
    def __init__(self, db_url, element_id, prop_dict):
        url = db_url + (self.id_url_template % element_id)
        element_dict = {}
        for k, v in self.DICT_TEMPLATE.items():
            element_dict[k] = v.replace('{db_url}',db_url).replace('{id}',str(element_id)) \
                    if not hasattr(v, 'keys') else v
        element_dict['data'] = prop_dict
        super(SkeletonBase, self).__init__(url, element_dict)

class SkeletonNode(SkeletonBase, LazyNode):
    DICT_TEMPLATE = {
        "outgoing_relationships" : "{db_url}node/{id}/relationships/out",
        "data" : {},
        "traverse" : "{db_url}node/{id}/traverse/{returnType}",
        "all_typed_relationships" : "{db_url}node/{id}/relationships/all/{-list|&|types}",
        "property" : "{db_url}node/{id}/properties/{key}",
        "self" : "{db_url}node/{id}",
        "outgoing_typed_relationships" : "{db_url}node/{id}/relationships/out/{-list|&|types}",
        "properties" : "{db_url}node/{id}/properties",
        "incoming_relationships" : "{db_url}node//relationships/in",
        "extensions" : {},
        "create_relationship" : "{db_url}node/{id}/relationships",
        "paged_traverse" : "{db_url}node/{id}/paged/traverse/{returnType}{?pageSize,leaseTime}",
        "all_relationships" : "{db_url}node/{id}/relationships/all",
        "incoming_typed_relationships" : "{db_url}node/{id}/relationships/in/{-list|&|types}"
    }

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
    return [_add_auth(cls.from_dict(v[0]), connections[using]) for v in result_table['data']]

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
            rel_by_url[d['self']] = _add_auth(LazyRelationship.from_dict(d), connections[using])
        else:
            node_by_url[d['self']] = _add_auth(LazyNode.from_dict(d), connections[using])
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

def _add_auth(n, conn):
    n._auth = conn._auth
    return n

class GremlinSnippet(object):
    def __init__(self, name, script, in_args=['results'], out_args=['results']):
        self.script = script
        self.in_args = in_args
        self.out_arg = out_arg
