import neo4jrestclient.client as neo4j
import neo4jrestclient.constants as neo_constants

from .. import DEFAULT_DB_ALIAS, connections
from ...utils import Enum, uniqify
from ...constants import ORDER_ATTR
from ...decorators import transactional, not_supported, alters_data, \
        not_implemented
from . import script_utils
from .script_utils import id_from_url

from django.db.models.query import QuerySet, EmptyQuerySet
from django.core import exceptions
from django.db.models.loading import get_model

from lucenequerybuilder import Q
from jexp import J

from collections import namedtuple
from operator import and_, or_
import itertools
import re

#python needs a bijective map... grumble... but a reg enum is fine I guess
#only including those operators currently being implemented
OPERATORS = Enum('EXACT', 'LT','LTE','GT','GTE','IN','RANGE','MEMBER','CONTAINS',
                 'STARTSWITH', 'MEMBER_IN')

ConditionTuple = namedtuple('ConditionTuple', ['field','value','operator','negate'])
class Condition(ConditionTuple):
    def __init__(self, *args, **kwargs):
        if 'value' in kwargs:
            if isinstance(kwargs['value'], list):
                kwargs['value'] = tuple(kwargs['value'])
        else:
            if len(args) > 1:
                if isinstance(args[1], list):
                    args = list(args)
                    args[1] = tuple(args[1])
        super(Condition, self).__init__( *args, **kwargs)

#TODO move this to settings.py
QUERY_CHUNK_SIZE = 50

#TODO these should be moved to constants
TYPE_REL = '<<TYPE>>'
INSTANCE_REL = '<<INSTANCE>>'
INTERNAL_RELATIONSHIPS = (TYPE_REL, INSTANCE_REL)


#TODO move to a util module
def not_none(it):
    return itertools.ifilter(None, it)

######################################
# IN-PYTHON QUERY CONDITION CHECKING #
######################################

def matches_condition(node, condition):
    """
    Return whether a node matches a filtering condition.
    """
    field, val, op, neg = condition
    passed = False
    #if this is an id field, the value should be the id
    if getattr(field, 'id', None):
        att = node.id
    elif node.properties.get(field.attname, None) is not None:
        att = node.properties[field.attname]
    else:
        att = None

    passed = (op is OPERATORS.EXACT and att == val) or \
             (op is OPERATORS.MEMBER and val in att) or \
             (op is OPERATORS.RANGE and val[0] < att < val[1]) or \
             (op is OPERATORS.LT and att < val) or \
             (op is OPERATORS.LTE and att <= val) or \
             (op is OPERATORS.GT and att > val) or \
             (op is OPERATORS.GTE and att >= val) or \
             (op is OPERATORS.IN and att in val) or \
             (op is OPERATORS.MEMBER_IN and any(a in val for a in att)) or \
             (op is OPERATORS.CONTAINS and val in att) or \
             (op is OPERATORS.STARTSWITH and att.startswith(val))
    if neg:
        passed = not passed
    return passed

def is_of_types(node, ts):
    #TODO return true if the provided node is of type t, or of a subtype of t
    return True

#########################
# QUERY CODE GENERATION #
#########################

def q_from_condition(condition):
    """
    Build a Lucene query from a filtering condition.
    """
    q = None
    field = condition.field
    attname = field.attname
    if condition.operator is OPERATORS.EXACT:
        q = Q(attname, field.to_neo_index(condition.value))
    elif condition.operator is OPERATORS.STARTSWITH:
        def escape_wilds(s):
            return str(s).replace('*','\*').replace('?','\?')
        q = Q(attname, '*%s*' % escape_wilds(condition.value), wildcard=True)
    elif condition.operator is OPERATORS.MEMBER:
        q = Q(attname, field.member_to_neo_index(condition.value))
    elif condition.operator is OPERATORS.IN:
        q = reduce(or_, (Q(attname, field.to_neo_index(v)) for v in condition.value))
    elif condition.operator is OPERATORS.MEMBER_IN:
        q = reduce(or_, (Q(attname, field.member_to_neo_index(v)) for v in condition.value))
    #FIXME OBOE with field.MAX + exrange, not sure it's easy to fix though...
    elif condition.operator in (OPERATORS.GT, OPERATORS.GTE, OPERATORS.LT,
                                OPERATORS.LTE, OPERATORS.RANGE): 
        if not field.indexed_range:
            raise exceptions.FieldError(
                'The {0} property is not configured for range '
                'indexing.'.format(field.attname))
        fieldtype = field._property_type()
        if condition.operator in (OPERATORS.GT, OPERATORS.GTE):
            if not hasattr(field, 'MAX'):
                raise exceptions.FieldError(
                    'The {0} property is not configured for gt/gte '
                    'queries.'.format(field.attname))
            if condition.operator is OPERATORS.GT:
                q = Q(attname, exrange=(field.to_neo_index(condition.value),
                                        field.to_neo_index(fieldtype.MAX)))
            else:
                q = Q(attname, inrange=(field.to_neo_index(condition.value),
                                        field.to_neo_index(fieldtype.MAX)))
        elif condition.operator in (OPERATORS.LT, OPERATORS.LTE):
            if not hasattr(fieldtype, 'MIN'):
                raise exceptions.FieldError(
                    'The {0} property is not configured for lt/lte '
                    'queries.'.format(field.attname))
            if condition.operator is OPERATORS.LT:
                q = Q(attname, exrange=(field.to_neo_index(fieldtype.MIN),
                                        field.to_neo_index(condition.value)))
            else:
                q = Q(attname, inrange=(field.to_neo_index(fieldtype.MIN),
                                        field.to_neo_index(condition.value)))
        elif condition.operator is OPERATORS.RANGE:
            if len(condition.value) != 2:
                raise exceptions.ValidationError('Range queries need upper and'
                                                ' lower bounds.')
            q = Q(condition.field.attname,
                inrange=[condition.field.to_neo_index(v)
                         for v in condition.value])
    else:
        return None
    if condition.negate:
        q = ~q
    return q

def js_expression_from_condition(condition, js_node):
    field, val, op, neg = condition
    name = field.attname

    #XXX assumption that attname is how the prop is stored (will change with
    #issue #30
    has_prop = js_node.hasProperty(name)
    get_prop = js_node.getProperty(name)
    if op == OPERATORS.EXACT:
        correct_val = get_prop == val
    elif op == OPERATORS.CONTAINS:
        correct_val = get_prop.indexOf(val) > -1
        pass
    elif op == OPERATORS.IN:
        correct_val = J('false')
        for v in val:
            correct_val |= get_prop == v
    elif op == OPERATORS.MEMBER:
        correct_val = get_prop.find(J('function(m){return %s;}' % str(J('m') == val))) == 1
    elif op == OPERATORS.MEMBER_IN:
        member_in = J('false')
        for v in val:
            member_in |= J('m') == v
        correct_val = get_prop.find(J('function(m){return %s;}' % str(member_in))) == 1
    elif op == OPERATORS.LT:
        correct_val = get_prop < val
    elif op == OPERATORS.LTE:
        correct_val = get_prop <= val
    elif op == OPERATORS.GT:
        correct_val = get_prop > val
    elif op == OPERATORS.GTE:
        correct_val = get_prop >= val
    elif op == OPERATORS.RANGE:
        correct_val = (get_prop <= val[0]) & (get_prop >= val[1])
    elif op == OPERATORS.STARTSWITH:
        correct_val = get_prop.lastIndexOf(val, 0) == 0
        pass
    else:
        raise NotImplementedError('Other operators are not yet implemented.')
    filter_exp = has_prop & correct_val
    if neg:
        filter_exp = ~filter_exp
    return filter_exp

def filter_expression_from_condition(condition):
    end_node = J('position').endNode()
    return js_expression_from_condition(condition, end_node)
    
def return_filter_from_conditions(conditions):
    exprs = [filter_expression_from_condition(c) for c in conditions]
    #construct an expr to exclude the first node and type nodes. consider
    #refactoring
    pos = J('position')
    length = pos.length()
    last_rel = pos.lastRelationship()
    exprs += [(length != 0) & (last_rel.getType().name() != "<<TYPE>>")]
    return (str(reduce(and_, exprs)) if exprs else 'true') + ';'

###################
# QUERY EXECUTION #
###################

def score_model_rel(field_name, bound_rel):
    """
    Scores a model's bound relationship on how likely it is to be the referrent
    of a user's select_related field.
    """
    score = 0
    if bound_rel.attname == field_name:
        score += 1
    if bound_rel.rel_type == field_name:
        score += .5
    return score

def cypher_rel_str(rel_type, rel_dir):
    dir_strings = ('<-%s-','-%s->')
    out = neo_constants.RELATIONSHIPS_OUT
    return dir_strings[rel_dir==out]%('[:`%s`]' % rel_type)

def cypher_from_fields(nodetype, fields):
    """
    Generates Cypher MATCH and RETURN expressions from `select_related()` style
    field strings.
    """
    #TODO this function is a great example of why there should be some greater
    # layer of abstraction between query code and script generation. a first 
    # step would be to write some CypherPrimitive, CypherList, etc.
    matches, returns = [], []
    reqd_fields = (field for i, field in enumerate(fields)
                   if not any(other_field.startswith(field) and field != other_field
                              for other_field in fields[i:]))

    for i, field in enumerate(reqd_fields):
        path_name = 'p%d' % i
        returns.append(path_name)

        rel_match_components = []
        cur_m = nodetype
        for step in field.split('__'):
            #try to propertly match a model field to the provided field string
            candidates_on_models = sorted((s for s in ((score_model_rel(step,r),r)
                for _,r in nodetype._meta._relationships.items()) if s > 0), reverse=True)
            #TODO provide for the case that there isn't a valid candidate
            choice = candidates_on_models[0]
            rel_match_components.append(
                cypher_rel_str(choice[1].rel_type, choice[1].direction))
        
        node_match_components = [] # Cypher node identifiers
        type_matches = [] # full Cypher type matching paths for return types
        for ri in xrange(len(rel_match_components)):
            return_node_name = '%s_r%d' % (path_name, ri)
            return_node_type_name = '%s_t' % return_node_name

            returns.extend((return_node_name, '%s.name' % return_node_type_name))

            node_match_components.append(return_node_name)
            type_matches.append('%s-[:`%s`]->%s' %
                    (return_node_type_name, INSTANCE_REL, return_node_name))

        model_match = ''.join(
            itertools.ifilter(None, itertools.chain.from_iterable(
                itertools.izip_longest(rel_match_components, node_match_components))))

        matches.append('%s=(s%s)'  % (path_name, model_match))
        matches.extend(type_matches)

    return 'MATCH %s RETURN %s' % (','.join(matches), ','.join(returns))

#XXX this will have to change significantly when issue #1 is worked on
#TODO this can be broken into retrieval and rebuilding functions
def execute_select_related(models=None, query=None, index_name=None,
                           fields=None, max_depth=1, model_type=None,
                           using=DEFAULT_DB_ALIAS):
    """
    Retrieves select_related models and and adds them to model caches.
    """
    if models is not None:
        if len(models) == 0:
            return
        #infer the database we're using
        model_dbs = [m.using for m in models if m.node]
        if len(set(model_dbs)) > 1:
            raise ValueError("Models to select_related should all be from the "
                             "same database.")
        else:
            using = model_dbs[0] if len(model_dbs) > 0 else using
        #infer the model type
        if model_type is None:
            model_type = type(models[0])
        start_expr = 'node(%s)' % ','.join(str(m.id) for m in models)
        start_depth = 1
    elif index_name and query:
        if model_type is None:
            raise ValueError("Must provide a model_type if using select_related"
                             " with an index query.")
        models = []
        start_expr = 'node:`%s`("%s")' % (index_name, str(query).replace('"','\\"'))
        start_depth = 0
    else:
        raise ValueError("Either a model set or an index name and query "
                            "need to be provided.")

    conn = connections[using]

    if fields is None:
        if max_depth < 1:
            raise ValueError("If no fields are provided for select_related, "
                                "max_depth must be > 0.")
        #the simple depth-only case
        #TODO it looks like this only works for depth=1...
        cypher_query = 'START s = %s '\
                       'MATCH p0=(s-[g*%d..%d]-p0_r0), p0_r0_t-[:`%s`]->p0_r0 '\
                       'RETURN p0, p0_r0, p0_r0_t.name'
        cypher_query %= (start_expr, start_depth, max_depth, INSTANCE_REL)
    elif fields:
        #build a match pattern + type check for each field
        match_and_return_expr = cypher_from_fields(model_type, fields)
        cypher_query = 'START s=%s %s'
        cypher_query %= (start_expr, match_and_return_expr)
    else:
        raise ValueError("Either a field list or max_depth must be provided "
                         "for select_related.")

    results = conn.cypher(cypher_query)

    #TODO this is another example of needing a cypher generation abstraction.
    paths = sorted(not_none(
                     results.get_rows(lambda c:re.match('p\d+$', c) is not None)),
                   key=lambda p:p['length'])
    nodes, types = [], []
    for path_i in itertools.count():
        path_name = 'p%d' % path_i
        if path_name not in results.column_names:
            break
        for node_i in itertools.count():
            return_node_name = '%s_r%s' % (path_name, node_i)
            return_node_type = '%s_t.name' % return_node_name
            if not (return_node_name in results.column_names or
                    return_node_type in results.column_names):
                break
            nodes = itertools.chain(nodes, results.get_rows(return_node_name))
            types = itertools.chain(types, results.get_rows(return_node_type))

    nodes = not_none(nodes)
    types = not_none(types)

    #put nodes in an id-lookup dict
    nodes = [script_utils.LazyNode.from_dict(d) for d in nodes]
    nodes_by_id = dict((n.id, n) for n in nodes)
    #add any nodes we've got from the models list
    if models is not None:
        nodes_by_id.update(dict((m.id, script_utils.LazyNode.from_dict(m.node._dic)) for m in models))

    #batch all relationships from paths and put em in a dict
    rels_by_id = {}
    rel_ids = []
    for p in paths:
        for rel_url in p['relationships']:
            rel_ids.append(id_from_url(rel_url))
    rels = script_utils.batch_rels(rel_ids, using)
    for r in rels:
        r.set_custom_node_lookup(nodes_by_id)
        rels_by_id[r.id] = r

    #build all the models, ignoring types that django hasn't loaded
    rel_nodes_types= ((n, get_model(*t.split(':')))
                      for n, t in itertools.izip(nodes, types))

    rel_models = (t._neo4j_instance(n) for n, t in rel_nodes_types if
        (t is not None) and (t._neo4j_instance(n) not in models))
    models_so_far = dict((m.id, m) for m in itertools.chain(models, rel_models))

    # TODO HACK set model rel caches to empty 
    # in the future, we'd like to properly mark a cache as 'filled', 'empty',
    # or 'unknown', to deal with deferred relationships versus those that have
    # been serviced by select_related. That will require doing more bookkeeping-
    # eg, knowing which models are at what depth in the max_depth case, and
    # which correspond to which field in the field case.
    # This covers the easy case, max_depth=1, and ignores the hard case of
    # dealing with a fields list or a greater depth.
    if fields is None and max_depth == 1:
        for m in models:
            for field_name, field in m._meta._relationships.items():
                #if rel is many side
                rel_on_model = getattr(m, field_name, None)
                if rel_on_model is not None and hasattr(rel_on_model, '_cache'):
                    rel_on_model._get_or_create_cache() #set the cache to loaded and empty
                else:
                    #otherwise single side
                    field._set_cached_relationship(m, None)

    #paths ordered by shortest to longest
    paths = sorted(paths, key=lambda v:v['length'])

    for path in paths:
        m = models_so_far[id_from_url(path['start'])]

        node_it = (id_from_url(url) for url in path['nodes'][1:])
        rel_it = (id_from_url(url) for url in path['relationships'])

        cur_m = m
        for rel_id, node_id in itertools.izip(rel_it, node_it):
            #make choice ab where it goes
            rel = rels_by_id[rel_id]
            rel.direction = neo_constants.RELATIONSHIPS_OUT if node_id == rel.end.id \
                            else neo_constants.RELATIONSHIPS_IN
            new_model = models_so_far[node_id]
            field_candidates = [(k,v) for k,v in cur_m._meta._relationships.items()
                                if str(v.rel_type)==str(rel.type) and v.direction == rel.direction]
            if len(field_candidates) < 1:
                continue
            elif len(field_candidates) > 1:
                raise ValueError("Too many model field candidates for "
                                 "returned path - there's an error in the "
                                 "Cypher query or your model definition.")
            field_name, field = field_candidates[0]

            #grab the model that should fit in this part of the path
            new_model = models_so_far[node_id]

            #if rel is many side
            rel_on_model = getattr(cur_m, field_name, None)
            if rel_on_model and hasattr(rel_on_model, '_cache'):
                rel_on_model._add_to_cache((rel, new_model))
                if field.ordered:
                    rel_on_model._cache.sort(
                        key=lambda r:r[0].properties.get(ORDER_ATTR, None))
            else:
                #otherwise single side
                field._set_cached_relationship(cur_m, new_model)
            cur_m = new_model



class Query(object):
    def __init__(self, nodetype, conditions=tuple(), max_depth=None, 
                 select_related_fields=[]):
        self.conditions = conditions
        self.nodetype = nodetype
        self.max_depth = max_depth
        self.select_related_fields = []
        self.select_related = bool(select_related_fields) or max_depth

    def add(self, prop, value, operator=OPERATORS.EXACT, negate=False):
    #TODO validate, based on prop type, etc
        return type(self)(self.nodetype, conditions = self.conditions +\
                          (Condition(prop, value, operator, negate),))

    def add_cond(self, cond):
        return type(self)(self.nodetype, conditions = self.conditions +\
                          tuple([cond]))

    def add_select_related(self, fields):
        self.select_related = True
        self.select_related_fields.extend(fields)

    def can_filter(self):
        return False #TODO not sure how we should handle this

    def set_limits(self, start, stop):
        #TODO will this ever be useful, given the tools the REST api gives us?
        pass

    def model_from_node(self, node):
        return self.nodetype._neo4j_instance(node)

    #TODO optimize query by using type info, instead of just returning the
    #proper type
    #TODO when does a returned index query of len 0 mean we're done?
    def execute(self, using):
        conditions = uniqify(self.conditions)

        #TODO exclude those that can't be evaluated against (eg exact=None, etc)
        id_conditions = []
        indexed = []
        unindexed = []

        for c in conditions:
            # if c.negate:
            #     raise NotImplementedError('Negative conditions (eg .exclude() are not supported')
            if getattr(c.field, 'id', False):
                id_conditions.append(c)
            elif c.field.indexed:
                indexed.append(c)
            else:
                unindexed.append(c)

        grouped_id_conds = itertools.groupby(id_conditions, lambda c: c.operator)
        id_lookups = dict(((k, list(l)) for k, l in grouped_id_conds))
        exact_id_lookups = list(id_lookups.get(OPERATORS.EXACT, []))
        #if we have an exact lookup, do it and return
        if len(exact_id_lookups) == 1:
            id_val = exact_id_lookups[0].value
            try:
                node = connections[using].nodes[int(id_val)]
                #TODO also check type!!
                if all(matches_condition(node, c) for c in itertools.chain(indexed, unindexed)):
                    #TODO DRY violation!
                    model_results = [self.model_from_node(node)]
                    if self.select_related:
                        sel_fields = self.select_related_fields
                        if not sel_fields: sel_fields = None
                        execute_select_related(models=model_results,
                                                fields=sel_fields,
                                                max_depth=self.max_depth)
                    yield model_results[0]
            except:
                pass
            return
        elif len(exact_id_lookups) > 1:
            raise ValueError("Conflicting exact id lookups - a node can't have two ids.")

        #if we have any id__in lookups, do the intersection and return
        in_id_lookups = list(id_lookups.get(OPERATORS.IN, []))
        if in_id_lookups:
            id_set = reduce(and_, (set(c.value) for c in in_id_lookups))
            if id_set:
                script = "results = Neo4Django.getVerticesByIds(ids)._();"
                nodes = connections[using].gremlin_tx(script, ids=list(id_set))
                ## TODO: HACKS: We don't know type coming out of neo4j-rest-client
                #               so we check it hackily here.
                if nodes == u'null':
                    return
                if hasattr(nodes, 'url'):
                    nodes = [nodes]
                model_results = [self.model_from_node(node) for node in nodes
                                 if all(matches_condition(node, c) for c in itertools.chain(indexed, unindexed))]
                #TODO DRY violation
                if self.select_related:
                    sel_fields = self.select_related_fields
                    if not sel_fields: sel_fields = None
                    execute_select_related(models=model_results,
                                            fields=sel_fields,
                                            max_depth=self.max_depth)

                for r in model_results:
                    yield r
                return
            else:
                return ## Emulates django's behavior
                                                      
        #TODO order by type - check against the global type first, so that if
        #we get an empty result set, we can return none? this kind of impedes Q
        #objects, though- revisit

        results = {} #TODO this could perhaps be cached - think about it
        filtered_results = set()

        index_by_name = dict((i.name, i) for i in (c.field.index(using) for c in indexed))

        #TODO order by type
        built_q = False
        cond_by_ind = itertools.groupby(indexed, lambda c:c.field.index(using).name)
        index_qs = []
        for index_name, group in cond_by_ind:
            index = index_by_name[index_name]
            q = None
            for condition in group:
                new_q = q_from_condition(condition)
                if not new_q:
                    break
                else:
                    built_q = True
                if q:
                    q &= new_q
                else:
                    q = new_q
            if q is not None:
                index_qs.append((index_name, str(q)))
        
        if built_q:
            result_set = script_utils.query_indices(index_qs, using)

            #filter for unindexed conditions, as well
            filtered_result = set(n for n in result_set \
                            if all(matches_condition(n, c) for c in conditions))
            model_results = [self.model_from_node(n)
                                for n in filtered_result]
            #TODO DRY violation
            if self.select_related:
                sel_fields = self.select_related_fields
                if not sel_fields: sel_fields = None
                execute_select_related(models=model_results,
                                        fields=sel_fields,
                                        max_depth=self.max_depth)

            for r in model_results:
                yield r
        else:
            return_filter = return_filter_from_conditions(unindexed + indexed)
            rel_types = [neo4j.Outgoing.get('<<TYPE>>'),
                         neo4j.Outgoing.get('<<INSTANCE>>')]
            type_node = self.nodetype._type_node(using)
            pages = type_node.traverse(types=rel_types,
                                            returnable=return_filter,
                                            page_size=QUERY_CHUNK_SIZE)
            for result_set in pages:
                filtered_result = set(n for n in result_set \
                                     if n not in filtered_results)
                filtered_results |= filtered_result
                model_results = [self.model_from_node(n)
                                 for n in filtered_result]
                #TODO DRY violation
                if self.select_related:
                    sel_fields = self.select_related_fields
                    if not sel_fields: sel_fields = None
                    execute_select_related(models=model_results,
                                           fields=sel_fields,
                                           max_depth=self.max_depth)
                for r in model_results:
                    yield r

        #if there are any unindexed
            #traverse for the provided types and their subtypes
            #for each page
                #return all nodes that match both indexed & unindexed conditions


        #pull all of these nodes (batch)
            #if any go against other, non-indexed conditions, toss them out
            #otherwise, we can yield those immediately
        #if there are any non-indexed fields, traverse
            #send a paged traversal filter with the current conditions
            #and with the list of ids, so we don't get overlap

        #TODO type stuff

        #TODO optimizations
        #group them by whether they share an index
        #get a list of ids that match for each group
            #memoize this (requires hashable Qs)
            #realize that some queries won't work (eg exact = None)
            #if one of the lists is zero, we can short-circuit to 0 elements returned

        #inner join the list together
        #return set.intersection(*results)

    def clone(self):
         return type(self)(self.nodetype, self.conditions, self.max_depth, self.select_related_fields)

#############
# QUERYSETS #
#############

def condition_from_kw(nodetype, keyval):
    pattern = re.compile('__+')
    terms = pattern.split(keyval[0])
    if not terms:
        pass #TODO error out
    elif len(terms) > 1:
        try:
            #get the corresponding operator
            op = getattr(OPERATORS, terms[1].upper())
        except AttributeError:
            raise NotImplementedError('The {0} operator is not yet implemented.'.format(terms[1]))
    else:
        op = OPERATORS.EXACT
    attname = terms[0]
    field = getattr(nodetype, attname)
    
    if op in (OPERATORS.RANGE, OPERATORS.IN, OPERATORS.MEMBER_IN):
        return Condition(field, tuple([field.to_neo(v) for v in keyval[1]]), op, False)
    else:
        return Condition(field, field.to_neo(keyval[1]), op, False)

def conditions_from_kws(nodetype, kwdict):
    return [condition_from_kw(nodetype, i) for i in kwdict.items()]

class NodeQuerySet(QuerySet):
    """
    Represents a lazy database lookup for a set of node models.
    """
    def __init__(self, model, using=DEFAULT_DB_ALIAS, query=None):
        super(NodeQuerySet, self).__init__(model=model, using=using, query=query or Query(model))
        #TODO is this actually necessary?
        self._app_label = model._meta.app_label

    ########################
    # PYTHON MAGIC METHODS #
    ########################

    @not_implemented
    def __deepcopy__(self, memo):
        pass

    @not_implemented
    def __getstate__(self):
        pass

    ####################################
    # METHODS THAT DO DATABASE QUERIES #
    ####################################

    def __getitem__(self, k):
        """"
        If k is a slice or there's a ._result_cache, use super __getitem__.
        Otherwise, iterate over the queryset, loading items into the cache
        one by one, and return last element of the cache.
        """
        if not isinstance(k, (int, long)) or (k < 0) or self._result_cache is not None:
            return super(NodeQuerySet, self).__getitem__(k)

        try:
            # ._fill_cache would be handy, but doesn't work when ._iter is None
            self._result_cache = []
            self._iter = self._iter or self.iterator()
            for _ in range(k + 1):
                self._result_cache.append(next(self._iter))
            return self._result_cache[-1]

        except self.model.DoesNotExist, e:
            raise IndexError(e.args)

    def iterator(self):
        using = self.db
        for model in self.query.execute(using):
            yield model

    @not_implemented
    def aggegrate(self, *args, **kwargs):
        pass

    @not_implemented
    def count(self, *args, **kwargs):
        pass

    #TODO leaving this todo for later transaction work
    @transactional
    def create(self, **kwargs):
        return super(NodeQuerySet, self).create(**kwargs)

    #TODO would be awesome if this were transactional
    def get_or_create(self, **kwargs):
        try:
            obj = self.get(**kwargs)
            created = False
        except:
            obj = self.create(**kwargs)
            created = True
        return (obj, created)

    @not_implemented
    def latest(self, field_name=None):
        pass

    @transactional
    def in_bulk(self, id_list):
        return dict((o.id, o) for o in self.model.objects.filter(id__in=id_list))
    
    @alters_data
    def delete(self):
        #TODO naive delete, should be seriously optimized- consider using
        # the batch api or some clever traversal deletion type stuff
        #TODO When new batch delete is put in place, will need to call pre_
        # and post_delete signals here; now they are covered in model delete()
        clone = self._clone()
        for obj in clone:
            obj.delete()

    @not_implemented
    @alters_data
    def update(self, **kwargs):
        pass

    @not_implemented
    def exists(self):
        pass

    ##################################################
    # PUBLIC METHODS THAT RETURN A QUERYSET SUBCLASS #
    ##################################################

    @not_implemented
    def values(self, *fields):
        pass

    @not_implemented
    def values_list(self, *fields, **kwargs):
        pass

    def dates(self, field_name, kind, order='ASC'):
        """
        Returns a list of datetime objects representing all available dates for
        the given field_name, scoped to 'kind'.
        """
        assert kind in ("month", "year", "day"), \
                "'kind' must be one of 'year', 'month' or 'day'."
        assert order in ('ASC', 'DESC'), \
                "'order' must be either 'ASC' or 'DESC'."
        return self._clone(klass=NodeDateQuerySet, setup=True,
                _field_name=field_name, _kind=kind, _order=order)

    ##################################################################
    # PUBLIC METHODS THAT ALTER ATTRIBUTES AND RETURN A NEW QUERYSET #
    ##################################################################

    #TODO what's that non-kw args being used for?
    def _filter_or_exclude(self, negate, *args, **kwargs):
        new_query = self.query.clone()
        for c in conditions_from_kws(self.model, kwargs):
            neg_c = Condition(*c[:-1], negate=negate)
            new_query = new_query.add_cond(neg_c)
        return self._clone(query=new_query)

    @not_implemented
    def complex_filter(self, filter_obj):
        pass

    def select_related(self, *fields, **kwargs):
        """
        Used the same way as in Django's ORM- select_related will load models
        from the graph up-front to minimize hitting the database.

        Some differences:
            - because we're dealing with a graph database, data will typically
            be highly connected. For this reason, depth defaults to 1.
            - we can't offer the same single-query transactional promises that
            Django's select_related offers, which means related objects might
            not be consistent. As usual, doing our best with what we have.
        """
        if 'depth' not in kwargs and not fields:
            kwargs['depth'] = 1
        return super(NodeQuerySet, self).select_related(*fields, **kwargs)

    def prefetch_related(self, *args, **kwargs):
        """
        Because of how Neo4j queries are built, this is just an alias for
        select_related.
        """
        return self.select_related(*args, **kwargs)

    @not_implemented
    def dup_select_related(self, other):
        pass

    @not_implemented
    def annotate(self, *args, **kwargs):
        pass

    @not_implemented
    def order_by(self, *field_names):
        pass
    
    @not_implemented
    def distinct(self, true_or_false=True):
        pass

    @not_supported
    def extra(self, *args, **kwargs):
        pass

    @not_implemented
    def reverse(self):
        pass

    #TODO can defer or only do anything useful? I think so...
    #using gremlin and some other magic in query, we might be able to swing
    #retrieving only particular fields.

    @not_implemented
    def defer(self, *fields):
        pass

    @not_implemented
    def only(self, *fields):
        pass

    ###################################
    # PUBLIC INTROSPECTION ATTRIBUTES #
    ###################################
   
    @property
    def db(self):
        "Return the database that will be used if this query is executed now"
        return self._db

    #################
    #  OLD COMMENTS #
    #################

    # filter TODO get should be based off this method somehow...
    #TODO should any non-indexed fields even be *allowed* to be used here? hm...
    # select_related  TODO explore implementing this with the traversal lib

    ###################
    # PRIVATE METHODS #
    ###################
    @not_supported
    def _as_sql(self, connection):
        pass
    
class NodeDateQuerySet(NodeQuerySet):

    def _setup_query(self):
        """
        Sets up any special features of the query attribute.
    
        Called by the _clone() method after initializing the rest of the
        instance.
        """
        self.query.clear_deferred_loading()
        self.query = self.query.clone(klass=sql.DateQuery, setup=True)
        self.query.select = []
        self.query.add_date_select(self._field_name, self._kind, self._order)
    
    def _clone(self, klass=None, setup=False, **kwargs):
        c = super(DateQuerySet, self)._clone(klass, False, **kwargs)
        c._field_name = self._field_name
        c._kind = self._kind
        if setup and hasattr(c, '_setup_query'):
            c._setup_query()
        return c

