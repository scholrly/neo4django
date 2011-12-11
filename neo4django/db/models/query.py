import neo4jrestclient.client as neo4j
import neo4jrestclient.constants as neo_constants

from neo4django.db import DEFAULT_DB_ALIAS, connections
from neo4django.utils import Enum, uniqify
from neo4django.constants import ORDER_ATTR
from neo4django.decorators import transactional, not_supported, alters_data, \
        not_implemented

from django.db.models.query import QuerySet
from django.core import exceptions
from django.db.models.loading import get_model

from lucenequerybuilder import Q
from jexp import J

from collections import namedtuple
from operator import and_, itemgetter
import itertools
import re

#python needs a bijective map... grumble... but a reg enum is fine I guess
#only including those operators currently being implemented
OPERATORS = Enum('EXACT', 'LT','LTE','GT','GTE','IN','RANGE','MEMBER','CONTAINS',
                 'STARTSWITH')

Condition = namedtuple('Condition', ['field','value','operator','negate'])

QUERY_CHUNK_SIZE = 10

#TODO these should be moved to constants
TYPE_REL = '<<TYPE>>'
INSTANCE_REL = '<<INSTANCE>>'
INTERNAL_RELATIONSHIPS = (TYPE_REL, INSTANCE_REL)

##################
# UTIL FUNCTIONS #
##################

def id_from_url(url):
    from urlparse import urlsplit
    from posixpath import dirname, basename
    path = urlsplit(url).path
    b = basename(path)
    return int(b if b else dirname(path))

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
    elif node.get(field.attname, None):
        att = node[field.attname]
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

def filter_expression_from_condition(condition):
    field, val, op, neg = condition
    name = field.attname
    end_node = J('position').endNode()
    #XXX assumption that attname is how the prop is stored (will change with
    #issue #30
    has_prop = end_node.hasProperty(name)
    get_prop = end_node.getProperty(name)
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

class LazyNode(neo4j.Node):
    """
    A version of the REST client node that doesn't update on init.
    """
    def __init__(self, url, dic):
        self._dont_update = True
        super(LazyNode, self).__init__(url, create=False)
        self._dic = dic.copy()
        self._dont_update = False

    def update(self, *args, **kwargs):
        if not self._dont_update:
            super(LazyNode, self).update(*args, **kwargs)

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

def cypher_match_from_fields(nodetype, fields):
    #TODO docstring
    matches = []
    for i, f in enumerate(fields):
        rel_matches = []
        cur_m = nodetype
        for step in f.split('__'):
            candidates_on_models = list(
                reversed(sorted(s for s in 
                                ((score_model_rel(step,r),r) for _,r in 
                                 nodetype._meta._relationships.items()) if s > 0)))
            choice = candidates_on_models[0]
            rel_matches.append(cypher_rel_str(choice[1].rel_type, choice[1].direction))
        matches.append('p%d=(s%d%sr%d)'  %(i, i, '()'.join(rel_matches), i))
        matches.append('pt%d=(t%d-[:`%s`]->r%d)' % (i, i, INSTANCE_REL, i))
    return matches

#XXX this will have to change significantly when issue #1 is worked on
#TODO this can be broken into retrieval and rebuilding functions
def execute_select_related(models=None, query=None, index_name=None,
                           fields=None, max_depth=1, model_type=None,
                           using=DEFAULT_DB_ALIAS):
    """
    Retrieves select_related models and and adds them to model caches.
    """
    if models:
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
        cypher_query = 'START s1 = %s '\
                       'MATCH p1=(s1-[g*%d..%d]-r1), pt1=(t1-[:`%s`]->r1) '\
                       'RETURN p1, r1, t1.name'
        cypher_query %= (start_expr, start_depth, max_depth, INSTANCE_REL)
        results = conn.cypher(cypher_query)
    elif fields:
        #get a new start point for each field
        starts = ['s%d=%s' % (i, start_expr) for i in xrange(len(fields))]
        #build a match pattern + type check for each field
        match_expr = ', '.join(cypher_match_from_fields(model_type, fields))
        return_expr = ', '.join('p%d, r%d, t%d.name' % (i,i,i)
                                for i in xrange(len(fields)))
        cypher_query = 'START %s '\
                       'MATCH %s '\
                       'RETURN %s'
        cypher_query %= (', '.join(starts), match_expr, return_expr)
        results = conn.cypher(cypher_query)

    else:
        raise ValueError("Either a fielf list of max_depth must be provided "
                         "for select_related.") #TODO

    def get_columns(column_name_pred, table):
        columns = [i for i,c in enumerate(table['columns']) if column_name_pred(c)]
        col_getter = itemgetter(*columns)
        return itertools.chain(*(rc if isinstance(rc, tuple) else (rc,)
                                 for rc in (col_getter(r)
                                            for r in table['data'])))
    paths = sorted(get_columns(lambda c:c.startswith('p'), results), key=lambda p:p['length'])
    nodes = get_columns(lambda c:c.startswith('r'), results)
    types = get_columns(lambda c:c.startswith('t'), results)

    #batch all relationships from paths
    rels_by_id = {}
    with conn.transaction():
        for p in paths:
            for rel_url in p['relationships']:
                rel_id = id_from_url(rel_url)
                if rel_id not in rels_by_id:
                    rels_by_id[rel_id] = conn.relationships[rel_id]

    #build all the models
    rel_models = (get_model(*t.split(':'))._neo4j_instance(n) for n, t in
                    ((LazyNode(r[0]['self'], r[0]), r[1])
                    for r in itertools.izip(nodes, types)))

    models_so_far = dict((m.id, m) for m in itertools.chain(models, rel_models))

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
                raise ValueError("No model field candidate for returned "
                                    "path - there's an error in the Cypher "
                                    "query or your model definition.")
            elif len(field_candidates) > 1:
                raise ValueError("Too many model field candidates for "
                                    "returned path - there's an error in the "
                                    "Cypher query or your model definition.")
            field_name, field = field_candidates[0]

            #if rel is many side
            rel_on_model = getattr(cur_m, field_name, None)
            if rel_on_model and hasattr(rel_on_model, '_cache'):
                rel_on_model._cache.append((rel, new_model))
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
            if getattr(c.field, 'id', False):
                id_conditions.append(c)
            elif c.field.indexed:
                indexed.append(c)
            else:
                unindexed.append(c)

        id_lookups = dict(itertools.groupby(id_conditions, lambda c: c.operator))
        exact_id_lookups = list(id_lookups.get(OPERATORS.EXACT, []))
        #if we have an exact lookup, do it and return
        if len(exact_id_lookups) == 1:
            id_val = exact_id_lookups[0].value
            try:
                node = connections[using].nodes[int(id_val)]
                #TODO also check type!!
                if all(matches_condition(node, c) for c in itertools.chain(indexed, unindexed)):
                    yield self.model_from_node(node)
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
                ext = connections[using].extensions['GremlinPlugin']
                gremlin_script = 'g.v(%s)'
                gremlin_script %= ','.join(str(i) for i in id_set)
                nodes = ext.execute_script(gremlin_script)
                #TODO also check type!!
                for node in nodes:
                    if all(matches_condition(node, c) for c in itertools.chain(indexed, unindexed)):
                        yield self.model_from_node(node)
                return
            else:
                raise ValueError('Conflicting id__in lookups - the intersection'
                                 ' of the queried id lists is empty.')
                                                      
                                                      
        #TODO order by type - check against the global type first, so that if
        #we get an empty result set, we can return none

        results = {} #TODO this could perhaps be cached - think2 about it
        filtered_results = set()

        #XXX: annoying workaround bc neo4jrestclient.client.Index doesn't tell equality
        #well/properly
        index_by_url = dict((i.url,i) for i in (c.field.index(using) for c in indexed))

        built_q = False
        cond_by_ind = itertools.groupby(indexed, lambda c:c.field.index(using).url)
        for index_url, group in cond_by_ind:
            index = index_by_url[index_url]
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
                result_set = set(index.query(q))
                #TODO results is currently worthless
                results[q] = result_set
                #TODO also needs to match at least one type, if any have been provided
                #filter for unindexed conditions, as well
                filtered_result = set(n for n in result_set \
                                if all(matches_condition(n, c) for c in conditions)\
                                    and n not in filtered_results)
                filtered_results |= filtered_result
                model_results = [self.model_from_node(n)
                                 for n in filtered_result]
                #TODO DRY violation
                if self.select_related:
                    sel_fields = self.select_related_fields
                    if not sel_fields: sel_fields = None
                    execute_select_related(index_name=index.name, query=q,
                                           fields=sel_fields,
                                           max_depth=self.max_depth,
                                           model_type=self.nodetype
                                          )
                for r in model_results:
                    yield r

        if not built_q:
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
    
    if op in (OPERATORS.RANGE, OPERATORS.IN):
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
            return self.get(**kwargs)
        except:
            return self.create(**kwargs)

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
        return self._clone(klass=NodeQuerySet, query=new_query)

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

