from django.db.models.query import QuerySet, EmptyQuerySet
from django.db.models.sql.query import Query as SQLQuery
from django.core import exceptions
from django.db.models.loading import get_model
from django.utils.datastructures import SortedDict

from lucenequerybuilder import Q

from collections import namedtuple, Iterable
from operator import and_, or_
import itertools
import re

import neo4jrestclient.client as neo4j
import neo4jrestclient.constants as neo_constants

from .. import DEFAULT_DB_ALIAS, connections
from ...utils import Enum, uniqify
from ...constants import ORDER_ATTR
from ...decorators import transactional, not_supported, alters_data, \
        not_implemented, borrows_methods
from . import script_utils
from .script_utils import id_from_url, LazyNode, _add_auth as add_auth
from . import aggregates

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

QUERY_CHUNK_SIZE = 100

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
    def escape_wilds(s):
        return str(s).replace('*','\*').replace('?','\?')
    if condition.operator is OPERATORS.EXACT:
        q = Q(attname, field.to_neo_index(condition.value))
    elif condition.operator is OPERATORS.STARTSWITH:
        q = Q(attname, '%s*' % escape_wilds(condition.value), wildcard=True)
    elif condition.operator is OPERATORS.CONTAINS:
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

def cypher_primitive(val):
    if isinstance(val, basestring):
        return '"%s"' % val
    elif isinstance(val, Iterable):
        return "[%s]" % ','.join(cypher_primitive(v) for v in val)
    return str(val)

def cypher_predicate_from_condition(element_name, condition):
    """
    Build a Cypher expression suitable for a WHERE clause from a condition.

    Arguments:
    element_name - a valid Cypher variable to filter against. This should be
    a column representing the field, eg "name", or another expression that will
    yield a value to filter against, like "node.name".
    condition - the condition for which we're generating a predicate
    """
    from .properties import StringProperty, ArrayProperty

    cypher = None

    # the neo4django field object
    field = condition.field

    #the value we're filtering against
    value = condition.value

    if condition.operator is OPERATORS.EXACT:
        cypher = ("%s! = %s" % 
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.GT:
        cypher = ("%s! > %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.GTE:
        cypher = ("%s! >= %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.LT:
        cypher = ("%s! < %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.LTE:
        cypher = ("%s! <= %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.RANGE:
        if len(condition.value) != 2:
            raise exceptions.ValidationError('Range queries need upper and'
                                            ' lower bounds.')
        cypher = ("(%s! >= %s) AND (%s! <= %s)" %
                  (element_name, cypher_primitive(value[0]), element_name,
                   cypher_primitive(value[1])))
    elif condition.operator is OPERATORS.MEMBER or \
            (isinstance(field._property, ArrayProperty) and 
             condition.operator is OPERATORS.CONTAINS):
        cypher = ("%s IN %s!" %
                  (cypher_primitive(value), element_name))
    elif condition.operator is OPERATORS.IN:
        cypher = ("%s! IN %s" % 
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.MEMBER_IN:
        cypher = ('ANY(someVar IN %s! WHERE someVar IN %s)' % 
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.CONTAINS:
        if isinstance(field._property, StringProperty):
            #TODO this is a poor man's excuse for Java regex escaping. we need
            # a better solution
            regex = ('.*%s.*' % re.escape(value))
            cypher = '%s! =~ %s' % (element_name, cypher_primitive(regex))
        else:
            raise exceptions.ValidationError('The contains operator is only'
                                             ' valid against string and array'
                                             ' properties.')
    elif condition.operator is OPERATORS.STARTSWITH:
        if not isinstance(field._property, StringProperty):
            raise exceptions.ValidationError(
                'The startswith operator is only valid against string '
                'properties.')
        cypher = ("LEFT(%s!, %d) = %s" %
                  (element_name, len(value), cypher_primitive(value)))
    else:
        raise NotImplementedError('Other operators are not yet implemented.')

    if condition.negate:
        cypher = "NOT (%s)" % cypher
    return cypher

def cypher_where_from_conditions(element_to_filter, conditions):
    """
    Build a Cypher WHERE clause based on a str Cypher element identifier that
    should resolve to a node or rel column in the final query, and an iterable
    of conditions.
    """
    # TODO need to change when we tackle #35 (ORs)
    where_exps = ("(%s)" % cypher_predicate_from_condition(
                    element_to_filter + '.' + c.field.attname, c)
                  for c in conditions)
    joined_exps = "AND".join(where_exps)
    return "WHERE %s\n" % joined_exps if joined_exps else ''

def cypher_order_by_term(element_name, field):
    """
    Return a term for an ORDER BY clause, like "n.name DESC", from a field like
    "-name".
    """
    stripped_field = field.strip()
    parts = stripped_field.split('-', 1)
    desc = ''
    field_name = parts[-1]
    if len(parts) > 1:
        desc = 'DESC'
    return '`%s`.`%s`? %s' % (element_name, field_name, desc)

def cypher_order_by_from_fields(element_name, ordering):

    #TODO it would be better if this happened at a higher level, so fields were
    # validated and default_ordering could be honored.
    cypher = ''
    if len(ordering) > 0:
        cypher = 'ORDER BY %s' % (
            ', '.join(cypher_order_by_term(element_name, f) for f in ordering))
    return cypher

class Clause(object):
    def as_cypher(self):
        return self.cypher_template % self.get_params()

class Clauses(list):
    def as_cypher(self):
        return ' '.join(c.as_cypher() if hasattr(c, 'as_cypher') else unicode(c)
                        for c in self)

class Start(Clause):
    cypher_template = 'START %(exprs)s'
    def __init__(self, start_assignments, cypher_params):
        """
        start_assignments - a dict of variable name keys and assignment
        expression values to make up a START clause. eg, `{'n':'node(5)'}`
        will lead to the expression `n=node(5)` in the output Cypher str
        cypher_params - a list of all Cypher parameters used in `start_exprs`.
        these won't affect the output, but are for later bookkeeping and
        manipulation
        """
        self.start_assignments = start_assignments
        self.cypher_params = cypher_params

    def get_params(self):
        return {
            'exprs' : ','.join('%s=%s' % (k, v)
                               for k, v in self.start_assignments.iteritems())
        }

class With(Clause):
    cypher_template = 'WITH %(fields)s %(limit)s %(match)s %(where)s'
    def __init__(self, field_dict, limit=None, where=None, match=None):
        self.field_dict = field_dict
        self.limit = limit
        self.where = where
        self.match = match

    def get_params(self):
        return {
            'fields':','.join('%s AS %s' % (alias, field)
                              for alias, field in self.field_dict.iteritems()),
            'limit': 'LIMIT %s' % str(self.limit) \
                    if self.limit is not None else '',
            'match': 'MATCH %s' % self.match if self.match else '',
            'where': 'WHERE %s' % self.where if self.where else '',
        }

class Return(Clause):
    cypher_template = 'RETURN %(fields)s %(order_by)s %(skip)s %(limit)s'
    def __init__(self, field_dict, limit=None, skip=None, order_by_terms=None):
        self.field_dict = field_dict
        self.limit = limit
        self.skip = skip
        self.order_by_terms = order_by_terms

    def get_params(self):
        return {
            'fields':','.join('%s AS %s' % (field, alias)
                              for alias, field in self.field_dict.iteritems()),
            'limit': 'LIMIT %s' % str(self.limit) \
                    if self.limit is not None else '',
            'skip': 'SKIP %d' % self.skip if self.skip else '',
            'order_by':'ORDER BY %s' % ','.join(self.order_by_terms) \
                       if self.order_by_terms else ''
        }

class Delete(Clause):
    cypher_template = 'DELETE %(fields)s'
    def __init__(self, fields):
        self.fields = fields

    def get_params(self):
        return {
            'fields':','.join(self.fields)
        }

class DeleteNode(Delete):
    cypher_template = 'WITH %(fields)s MATCH %(field_matches)s '\
                      'DELETE %(fields_and_rels)s'
    def get_params(self):
        field_rels = ['%s_r' % f for f in self.fields]
        params = {
            'fields':','.join(self.fields),
            'fields_and_rels':','.join(self.fields + field_rels),
            'field_matches':','.join('(%s)-[%s]-()' % (f, r)
                                     for f, r in zip(self.fields, field_rels))
        }
        return params

def cypher_rel_str(rel_type, rel_dir,identifier=None):
    dir_strings = ('<-%s-','-%s->')
    out = neo_constants.RELATIONSHIPS_OUT
    id_str = '`%s`' % identifier if identifier is not None else ''
    return dir_strings[rel_dir==out]%('[%s:`%s`]' % (id_str, rel_type))

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
        cypher_query = 'START s = %s '\
                       'MATCH p0=(s-[g*%d..%d]-p0_r0), p0_r0_t-[:`%s`]->p0_r0 '\
                       'WHERE NONE(r in g WHERE type(r) = "<<INSTANCE>>")'\
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
                     results.get_all_rows(lambda c:re.match('p\d+$', c) is not None)),
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
            nodes = itertools.chain(nodes,
                                    results.get_all_rows(return_node_name))
            types = itertools.chain(types,
                                    results.get_all_rows(return_node_type))

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

            if node_id not in models_so_far:
                # we've loaded a node outside of neo4django, or of a type
                # not yet loaded by neo4django. skip it.
                continue
            
            #make choice ab where it goes
            rel = rels_by_id[rel_id]
            rel.direction = neo_constants.RELATIONSHIPS_OUT if node_id == rel.end.id \
                            else neo_constants.RELATIONSHIPS_IN


            field_candidates = [(k,v) for k,v in cur_m._meta._relationships.items()
                                if str(v.rel_type)==str(rel.type) and v.direction == rel.direction]
            if len(field_candidates) < 1:
                # nowhere to put the node- it's either related outside
                # neo4django or something else is going on
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

# we want some methods of SQLQuery but don't want the burder of inheriting
# everything. these methods are pulled off django.db.models.sql.query.Query
QUERY_PASSTHROUGH_METHODS = ('set_limits','clear_limits','can_filter',
                             'add_ordering', 'clear_ordering')

@borrows_methods(SQLQuery, QUERY_PASSTHROUGH_METHODS)
class Query(object):
    aggregates_module = aggregates
    def __init__(self, nodetype, conditions=tuple(), max_depth=None, 
                 select_related_fields=[]):
        self.conditions = conditions
        self.nodetype = nodetype
        self.max_depth = max_depth
        self.select_related_fields = list(select_related_fields)
        self.select_related = bool(select_related_fields) or max_depth

        self.return_fields = {'n':'n'}

        self.aggregates = {}
        self.distinct_fields = []

        self.start_clause = None
        self.start_clause_param_func = lambda : {}
        self.with_clauses = []
        self.end_clause = None

        self.clear_limits()
        self.clear_ordering()

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

    def add_aggregate(self, aggregate, model, alias, is_summary):
        opts = model._meta
        if '__' in aggregate.lookup:
            raise NotImplementedError('Only simple field aggregates are'
                                      ' currently supported.')
        field_alias = aggregate.lookup
        try:
            source = opts.get_field(field_alias)
        except: #TODO fix bare except (FieldDoesNotExist)
            source = field_alias

        aggregate.add_to_query(self, alias, col=field_alias, source=source,
                               is_summary=is_summary)

    def add_with(self, field_dict, **kwargs):
        self.with_clauses.append(With(field_dict, **kwargs))

    def set_start_clause(self, clause, param_dict_or_func=None):
        """
        clause - anything whose `as_cypher()` method returns a valid beginning
        of a Cypher query as a str. Additional elements, like MATCH queries, can
        be included as well.
        """
        self.start_clause = clause
        if param_dict_or_func is None:
            param_dict_or_func = {}
        self.start_clause_param_func = param_dict_or_func if \
                callable(param_dict_or_func) else lambda:param_dict_or_func

    def get_start_clause_and_param_dict(self):
        # TODO if a clause has been set, return that
        # otherwise, compute it from conditions
        # (requires a refactor from as_groovy())
        return self.start_clause, self.start_clause_param_func()

    def model_from_node(self, node):
        return self.nodetype._neo4j_instance(node)

    def clone(self):
        clone = type(self)(self.nodetype, self.conditions, self.max_depth,
                           self.select_related_fields)
        clone.order_by = self.order_by
        clone.return_fields = self.return_fields
        clone.aggregates = self.aggregates
        clone.high_mark = self.high_mark
        clone.low_mark = self.low_mark
        clone.start_clause = self.start_clause
        clone.start_clause_param_func = self.start_clause_param_func
        clone.with_clauses = self.with_clauses
        clone.end_clause = self.end_clause
        return clone

    def get_aggregation(self, using):
        query = self.clone()
        def make_aggregate_of_n(agg):
            from .properties import BoundProperty, Property
            #TODO HACK when we start adding more columns this will get messy
            #TODO HACK this should resolve the field, then use field.attname
            # or similar to get the actual db prop name
            #TODO HACK this is just to cover weird cases like '*'...
            agged_over = 'n.%s' % agg.prop_name \
                    if isinstance(agg.source, (BoundProperty, Property)) \
                    else agg.prop_name
            return type(agg)(agged_over, source=agg.source,
                             is_summary=agg.is_summary)
        query.return_fields = SortedDict(
            (alias, make_aggregate_of_n(agg).as_cypher())
            for alias, agg in query.aggregates.iteritems())
        groovy, params = query.as_groovy(using)
        result_set = connections[using].gremlin_tx(groovy, raw=True, **params)
        # TODO HACK this only works for one aggregate
        return {query.return_fields.keys()[0]:result_set[0]}

    def as_groovy(self, using):
        conditions = uniqify(self.conditions)

        #TODO exclude those that can't be evaluated against (eg exact=None, etc)
        # TODO... those can probably be evaluated against now with Cypher!
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

        #TODO error out or handle other id conditions (eg gt, lt)
        grouped_id_conds = itertools.groupby(id_conditions, lambda c: c.operator)
        id_lookups = dict(((k, list(l)) for k, l in grouped_id_conds))

        exact_id_lookups = list(id_lookups.get(OPERATORS.EXACT, []))
        if len(exact_id_lookups) > 1:
            raise ValueError("Conflicting exact id lookups - a node can't have two ids.")

        in_id_lookups = list(id_lookups.get(OPERATORS.IN, []))

        index_by_name = dict((i.name, i) for i in 
                             (c.field.index(using) for c in indexed))

        #TODO order by type
        # build lucene queries for index-based conditions
        cond_by_ind = itertools.groupby(indexed,
                                        lambda c:c.field.index(using).name)
        index_qs = []
        for index_name, group in cond_by_ind:
            index = index_by_name[index_name]
            q = None
            for condition in group:
                new_q = q_from_condition(condition)
                if not new_q:
                    break
                if q:
                    q &= new_q
                else:
                    q = new_q
            if q is not None:
                index_qs.append((index_name, str(q)))
        
        # use filtered conditions, ids, OR a type tree traversal as a cypher
        # START, then unfiltered as a WHERE

        start_clause, cypher_params = self.get_start_clause_and_param_dict()

        # add the typeNodeId param, either for type verification or initial
        # type tree traversal
        cypher_params['typeNodeId'] = self.nodetype._type_node(using).id

        type_restriction_expr = """
        n<-[:`<<INSTANCE>>`]-()<-[:`<<TYPE>>`*0..]-typeNode
        """

        where_clause = cypher_where_from_conditions('n', itertools.chain(
            unindexed + indexed))
        
        with_clauses = self.with_clauses

        order_by = [cypher_order_by_term('n', field) for field in self.order_by] \
                or None
        limit = self.high_mark - self.low_mark if self.high_mark is not None \
                else None
        return_clause = Return(self.return_fields, skip=self.low_mark,
                               limit=limit, order_by_terms=order_by)\
                if self.end_clause is None else self.end_clause

        groovy_script = None
        params = {
            #TODO HACK need a generalization
            'returnColumn':self.return_fields.keys()[0]
        }

        # TODO none of these queries but the last properly take type into
        # account.
        if len(in_id_lookups) > 0 or len(exact_id_lookups) > 0:
            id_set = reduce(and_, (set(c.value) for c in in_id_lookups)) \
                    if in_id_lookups else set([])
            if len(exact_id_lookups) > 0:
                exact_id = exact_id_lookups[0].value
                if id_set and exact_id not in id_set:
                    raise ValueError("Conflicting id__exact and id__in lookups"
                                     " - a node can't have two ids.")
                else:
                    id_set = set([exact_id])

            if len(id_set) >= 1:
                start_clause = start_clause or Start({'n':'node({startParam})'},
                                                 ['startParam'])
                groovy_script = """
                    results = []
                    existingStartIds = Neo4Django.getVerticesByIds(start).\
                            collect{it.id}
                    cypherParams['startParam'] = existingStartIds
                    table = Neo4Django.cypher(cypherQuery,cypherParams)
                    results = table.columnAs(returnColumn)
                    """
                params['start'] = list(id_set)
            else:
                # XXX None is returned, meaning an empty result set
                return (None, None)
        elif len(index_qs) > 0:
            start_clause = start_clause or Start({'n':'node({startParam})'},
                                                 ['startParam'])
            groovy_script = """
                results = []
                startIds = Neo4Django.queryNodeIndices(startQueries)\
                            .collect{it.id}
                cypherParams['startParam'] = startIds
                table = Neo4Django.cypher(cypherQuery, cypherParams)
                results = table.columnAs(returnColumn)
                """
            params['startQueries'] = index_qs
        else:
            #TODO move this to being index-based
            if start_clause is None:
                match_clause = 'MATCH %s' % type_restriction_expr
                start_clause = Clauses([Start({'typeNode':'node({typeNodeId})'},
                                              ['typeNodeId']),
                                        match_clause])
                # we don't need an additional type restriction since it's
                # handled in the start
                type_restriction_expr = None
            groovy_script = """
                results = []
                table = Neo4Django.cypher(cypherQuery, cypherParams)
                results = table.columnAs(returnColumn)
                """

        # make sure the start clause includes the typeNode
        if isinstance(start_clause, Clauses):
            start_clause, extra_start_clauses = start_clause[0], start_clause[1:]
        else:
            extra_start_clauses = []
        if 'typeNode' not in start_clause.start_assignments:
            start_clause.start_assignments['typeNode'] = 'node({typeNodeId})'
            start_clause.cypher_params += ['typeNodeId']
        start_clause = Clauses([start_clause] + extra_start_clauses)

        if type_restriction_expr:
            if where_clause:
                where_clause = ' AND '.join((where_clause,
                                             type_restriction_expr))
            else:
                where_clause = 'WHERE ' + type_restriction_expr

        str_clauses = [start_clause.as_cypher(), where_clause] + \
                      [c.as_cypher() for c in with_clauses] + \
                      [return_clause.as_cypher()]

        params['cypherQuery'] = ' '.join(str_clauses) + ';'
        params['cypherParams'] = cypher_params

        return groovy_script, params

    #TODO optimize query by using type info, instead of just returning the
    #proper type
    #TODO when does a returned index query of len 0 mean we're done?
    def execute(self, using):
        conn = connections[using]

        groovy, params = self.as_groovy(using)

        raw_result_set = conn.gremlin_tx(groovy, **params) \
                if groovy is not None else []

        #make the result_set not insane (properly lazy)
        result_set = [add_auth(LazyNode.from_dict(d), conn)
                        for d in raw_result_set._list] if raw_result_set else []
        
        model_results = [self.model_from_node(n)
                            for n in result_set]

        if self.select_related:
            sel_fields = self.select_related_fields
            if not sel_fields: sel_fields = None
            execute_select_related(models=model_results,
                                    fields=sel_fields,
                                    max_depth=self.max_depth)

        for r in model_results:
            yield r

    def delete(self, using):
        clone = self.clone()
        clone.end_clause = DeleteNode(['n'])
        for m in clone.execute(using):
            pass

    def get_count(self, using):
        from django.db.models import Count
        obj = self.clone()
        obj.add_aggregate(Count('*'), self.nodetype, 'count', True)
        aggregation = obj.get_aggregation(using)
        return aggregation.get('count', None)

    def has_results(self, using):
        obj = self.clone()
        obj.add_with({'n':'n'}, limit=1)
        return obj.get_count(using) > 0

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
        if not isinstance(k, (int, long)) or (k < 0) or \
           self._result_cache is not None:
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

    def __contains__(self, value):
        if self._result_cache is None:
            while True:
                i = 0
                try:
                    if self[i] == value:
                        return True
                    i+=1
                except IndexError:
                    return False
            pass
        return super(NodeQuerySet, self).__contains__(value)

    def iterator(self):
        using = self.db
        if not self.query.can_filter():
            for model in self.query.execute(using):
                yield model
        else:
            start = 0
            stop = QUERY_CHUNK_SIZE
            while True:
                clone = self.query.clone()
                clone.set_limits(start, stop)
                piece = list(clone.execute(using))
                for model in piece:
                    yield model
                if len(piece) < QUERY_CHUNK_SIZE:
                    break
                start = stop
                stop += QUERY_CHUNK_SIZE

    @not_implemented
    def aggegrate(self, *args, **kwargs):
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
        self.query.delete(self.db)

    @not_implemented
    @alters_data
    def update(self, **kwargs):
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

    @not_implemented
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
    def distinct(self, true_or_false=True):
        pass

    @not_supported
    def extra(self, *args, **kwargs):
        pass

    @not_implemented
    def reverse(self):
        pass

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

