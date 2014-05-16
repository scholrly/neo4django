from django.db.models import Q
from django.db.models.query import QuerySet
from django.db.models.sql import subqueries
from django.core import exceptions
from django.db.models.loading import get_model
from django.utils.datastructures import SortedDict

from lucenequerybuilder import Q as LQ

from collections import namedtuple, defaultdict
from operator import and_, or_
import itertools
import re

import neo4jrestclient.constants as neo_constants

from .. import DEFAULT_DB_ALIAS, connections
from ...utils import Enum, uniqify, not_none
from ...constants import ORDER_ATTR
from ...decorators import (transactional,
                           not_supported,
                           alters_data,
                           not_implemented,
                           borrows_methods)

from .cypher import (Clauses, Start, NodeComponent, RelationshipComponent, Path,
                     Match, With, Set, Return, ColumnExpression, OrderByTerm,
                     OrderBy, DeleteNode, cypher_primitive)

from . import script_utils
from .script_utils import id_from_url, LazyNode, _add_auth as add_auth
from . import aggregates

#python needs a bijective map... grumble... but a reg enum is fine I guess
#only including those operators currently being implemented
OPERATORS = Enum('EXACT', 'IEXACT', 'LT', 'LTE', 'GT', 'GTE', 'IN', 'RANGE', 'MEMBER',
                 'CONTAINS', 'ICONTAINS', 'STARTSWITH', 'ISTARTSWITH',
                 'ENDSWITH', 'IENDSWITH', 'REGEX', 'IREGEX', 'MEMBER_IN',
                 'YEAR', 'MONTH', 'DAY', 'ISNULL')

ConditionTuple = namedtuple('ConditionTuple', ['field', 'value', 'operator', 'path'])


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
        super(Condition, self).__init__(*args, **kwargs)


QUERY_CHUNK_SIZE = 100

#TODO these should be moved to constants
TYPE_REL = '<<TYPE>>'
INSTANCE_REL = '<<INSTANCE>>'
INTERNAL_RELATIONSHIPS = (TYPE_REL, INSTANCE_REL)


#########################
# QUERY CODE GENERATION #
#########################

def clone_q(q):
    children = [clone_q(child) if isinstance(child, Q) else child
                for child in q.children]
    new_q = Q(*children)
    new_q.negated = q.negated
    new_q.connector = q.connector
    return new_q


def condition_from_kw(nodetype, keyval):
    pattern = re.compile('__+')
    terms = pattern.split(keyval[0])
    explicit_op = False

    if not terms:
        pass  # TODO error out
    elif len(terms) > 1:
        try:
            #get the corresponding operator
            op = getattr(OPERATORS, terms[-1].upper())
        except AttributeError:
            op = OPERATORS.EXACT
        else:
            explicit_op = True
    else:
        op = OPERATORS.EXACT

    path = terms[:-1] if explicit_op else terms[:]

    attname = None

    cur_m = nodetype
    for level, step in enumerate(path):
        #TODO DRY violation, this needs to be refactored to share code with
        # the select_related machinery, and possibly reuse Django methods for
        # following these paths
        rels = getattr(cur_m._meta, '_relationships', {}).items()
        candidates_on_models = sorted((s for s in ((score_model_rel(step, r), r)
                                                   for _, r in rels)
                                       if s[0] > 0), reverse=True)
        if len(candidates_on_models) < 1:
            # if there's no candidate, it could be an error *OR* it could be
            # a property at the end of the path
            if level == len(path) - 1:
                attname = path[-1]
                path = path[:-1]
                break
            else:
                raise exceptions.ValidationError("Cannot find referenced field "
                                                 "`%s` from model %s." %
                                                 (keyval[0], nodetype.__name__))
        rel_choice = candidates_on_models[0][-1]
        cur_m = (rel_choice.target_model if not rel_choice.target_model is cur_m
                 else rel_choice.source_model)

    attname = attname or 'id'

    try:
        field = getattr(cur_m, attname)
    except AttributeError:
        raise exceptions.ValidationError("Cannot find referenced field `%s` from model %s." %
                                         (keyval[0], nodetype.__name__))

    if op in (OPERATORS.RANGE, OPERATORS.IN, OPERATORS.MEMBER_IN):
        return Condition(field, tuple([field.to_neo(v) for v in keyval[1]]),
                         op, path)
    else:
        return Condition(field, field.to_neo(keyval[1]), op, path)


def lucene_query_from_condition(condition):
    """
    Build a Lucene query from a kw pair like those making up Q objects, eg
    ('name__exact','Sarah').
    """
    lq = None
    field = condition.field
    attname = field.attname

    def escape_wilds(s):
        return str(s).replace('*', '\*').replace('?', '\?')
    if condition.operator is OPERATORS.EXACT:
        lq = LQ(attname, field.to_neo_index(condition.value))
    elif condition.operator is OPERATORS.STARTSWITH:
        lq = LQ(attname, '%s*' % escape_wilds(condition.value), wildcard=True)
    elif condition.operator is OPERATORS.CONTAINS:
        lq = LQ(attname, '*%s*' % escape_wilds(condition.value), wildcard=True)
    elif condition.operator is OPERATORS.MEMBER:
        lq = LQ(attname, field.member_to_neo_index(condition.value))
    elif condition.operator is OPERATORS.IN:
        lq = reduce(or_, (LQ(attname, field.to_neo_index(v))
                          for v in condition.value))
    elif condition.operator is OPERATORS.MEMBER_IN:
        lq = reduce(or_, (LQ(attname, field.member_to_neo_index(v))
                          for v in condition.value))
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
                lq = LQ(attname, exrange=(field.to_neo_index(condition.value),
                                          field.to_neo_index(fieldtype.MAX)))
            else:
                lq = LQ(attname, inrange=(field.to_neo_index(condition.value),
                                          field.to_neo_index(fieldtype.MAX)))
        elif condition.operator in (OPERATORS.LT, OPERATORS.LTE):
            if not hasattr(fieldtype, 'MIN'):
                raise exceptions.FieldError(
                    'The {0} property is not configured for lt/lte '
                    'queries.'.format(field.attname))
            if condition.operator is OPERATORS.LT:
                lq = LQ(attname, exrange=(field.to_neo_index(fieldtype.MIN),
                                          field.to_neo_index(condition.value)))
            else:
                lq = LQ(attname, inrange=(field.to_neo_index(fieldtype.MIN),
                                          field.to_neo_index(condition.value)))
        elif condition.operator is OPERATORS.RANGE:
            if len(condition.value) != 2:
                raise exceptions.ValidationError('Range queries need upper and lower bounds.')
            lq = LQ(condition.field.attname,
                    inrange=[condition.field.to_neo_index(v)
                             for v in condition.value])
    else:
        return None
    return lq


def condition_tree_from_q(nodetype, q, predicate=lambda x:True):
    """
    Returns a new Q tree with kwargs pairs replaced by conditions. Any
    conditions that don't meet an optional predicate will be removed.
    """
    if not isinstance(q, Q):
        if isinstance(q, Condition):
            return q
        return condition_from_kw(nodetype, q)
    new_q = clone_q(q)
    children = [condition_tree_from_q(nodetype, child, predicate=predicate)
                for child in new_q.children]
    new_q.children = filter(predicate, children)
    new_q.children_filtered = len(new_q.children) != len(q.children)
    return new_q


def condition_tree_leaves(q):
    """
    A generator to iterate through all meaningful leaves in a Q tree.
    """
    if not isinstance(q, Q):
        yield q
    else:
        for child in q.children:
            for leaf in condition_tree_leaves(child):
                yield leaf


def lucene_query_from_condition_tree(cond_q):
    """
    Unpack a Q tree with Condition children, building a Lucene query tree as
    we go.
    """
    if not isinstance(cond_q, Q):
        return lucene_query_from_condition(cond_q)
    if len(cond_q.children) > 0:
        children = [lucene_query_from_condition_tree(c)
                    for c in cond_q.children]
        children = filter(lambda x: bool(x), children)
        if len(children) > 0:
            op = and_ if cond_q.connector == 'AND' else or_
            lucene_query = reduce(op, children)
            if cond_q.negated:
                lucene_query = ~lucene_query
            return lucene_query


def lucene_query_and_index_from_q(using, nodetype, q):
    """
    Return an index name / Lucene query pair based on a given database, node
    type, and Q filter tree- which can have a mix of kwargs or Condition leaves.
    """
    # crawl the Q tree and prune all non-indexed fields. collect all indexed
    # non-rel-spanning fields, but drop any that have been OR'd against

    # XXX hack to get around lack of real closure support
    prop_indexes = set([])

    def predicate(cond):
        if isinstance(cond, Q):
            # exclude OR'd fields that aren't *all* indexed, as they can't
            # use an index to help. we use the "children_filtered" bool set by
            # condition_tree_from_q
            return not(cond.connector == 'OR' and
                       getattr(cond, 'children_filtered', False))
        # make sure the field is indexed, isn't a rel-spanning field,
        # and isn't an id field
        if len(cond.path) < 1 and cond.field.indexed \
           and not getattr(cond.field, 'id', False):
            index = cond.field.index(using)
            prop_indexes.add(index)
            if len(prop_indexes) > 1:
                raise exceptions.ValidationError("Complex filters cannot refer "
                                                 "to two indexed properties "
                                                 "that don't share an index.")
            return True

    cond_q = condition_tree_from_q(nodetype, q, predicate=predicate)
    if len(prop_indexes) == 0 or not predicate(cond_q):
        return None
    index = next(iter(prop_indexes))
    return (index.name, lucene_query_from_condition_tree(cond_q))


def cypher_predicate_from_condition(element_name, condition):
    """
    Build a Cypher expression suitable for a WHERE clause from a condition.

    Arguments:
    element_name - a valid Cypher variable to filter against. This should be
    a column representing the field, eg "name", or another expression that will
    yield a value to filter against, like "node.name".
    condition - the condition for which we're generating a predicate
    """
    from .properties import (StringProperty, ArrayProperty, DateProperty,
                             DateTimeProperty)
    from .relationships import BoundRelationship

    cypher = None

    # the neo4django field object
    field = condition.field

    #the value we're filtering against
    value = condition.value

    # if the operator is a simple case-insensitive op, lower-case the value
    # and wrap the element_name in LOWER
    # NB - this won't work for complex cases, eg iregex
    if condition.operator in (OPERATORS.IEXACT, OPERATORS.ICONTAINS,
                              OPERATORS.ISTARTSWITH, OPERATORS.IENDSWITH):
        value = value.lower()
        element_name = 'LOWER(%s)' % element_name

    if condition.operator in (OPERATORS.EXACT, OPERATORS.IEXACT):
        cypher = ("%s = %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.GT:
        cypher = ("%s > %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.GTE:
        cypher = ("%s >= %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.LT:
        cypher = ("%s < %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.LTE:
        cypher = ("%s <= %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.RANGE:
        if len(condition.value) != 2:
            raise exceptions.ValidationError('Range queries need upper and lower bounds.')
        cypher = ("(%s >= %s) AND (%s <= %s)" %
                  (element_name, cypher_primitive(value[0]), element_name,
                   cypher_primitive(value[1])))
    elif (condition.operator is OPERATORS.MEMBER or
          (condition.operator is OPERATORS.CONTAINS and
           isinstance(field._property, ArrayProperty))):
        cypher = ("%s IN %s" %
                  (cypher_primitive(value), element_name))
    elif condition.operator is OPERATORS.IN:
        cypher = ("%s IN %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.MEMBER_IN:
        cypher = ('ANY(someVar IN %s WHERE someVar IN %s)' %
                  (element_name, cypher_primitive(value)))
    elif condition.operator in (OPERATORS.CONTAINS, OPERATORS.ICONTAINS):
        if isinstance(field._property, StringProperty):
            #TODO this is a poor man's excuse for Java regex escaping. we need
            # a better solution
            regex = ('.*%s.*' % re.sub('"\'`;:{}\(\)\|', '', value) )
            cypher = '%s =~ %s' % (element_name, cypher_primitive(regex))
        else:
            raise exceptions.ValidationError('The contains operator is only'
                                             ' valid against string and array '
                                             'properties.')
    elif condition.operator in (OPERATORS.STARTSWITH, OPERATORS.ISTARTSWITH):
        if not isinstance(field._property, StringProperty):
            raise exceptions.ValidationError(
                'The startswith operator is only valid against string '
                'properties.')
        cypher = ("LEFT(%s, %d) = %s" %
                  (element_name, len(value), cypher_primitive(value)))
    elif condition.operator in (OPERATORS.ENDSWITH, OPERATORS.IENDSWITH):
        if not isinstance(field._property, StringProperty):
            raise exceptions.ValidationError(
                'The endswith operator is only valid against string '
                'properties.')
        cypher = ("RIGHT(%s, %d) = %s" %
                  (element_name, len(value), cypher_primitive(value)))
    elif condition.operator in (OPERATORS.REGEX, OPERATORS.IREGEX):
        if not isinstance(field._property, StringProperty):
            raise exceptions.ValidationError(
                'The regex operator is only valid against string '
                'properties.')
        if condition.operator is OPERATORS.IREGEX:
            value = '(?i)' + value
        cypher = ("%s =~ %s" %
                  (element_name, cypher_primitive(value)))
    elif condition.operator is OPERATORS.YEAR:
        if not isinstance(field._property, (DateProperty, DateTimeProperty)):
            raise exceptions.ValidationError(
                'The year operator is only valid against date-based '
                'properties.')
        cypher = ("SUBSTRING(%s, 0, 4) = %s" %
                  (element_name, cypher_primitive(unicode(value).zfill(4))))
    elif condition.operator is OPERATORS.MONTH:
        if not isinstance(field._property, (DateProperty, DateTimeProperty)):
            raise exceptions.ValidationError(
                'The month operator is only valid against date-based '
                'properties.')
        cypher = ("SUBSTRING(%s, 5, 2) = %s" %
                  (element_name, cypher_primitive(unicode(value).zfill(2))))
    elif condition.operator is OPERATORS.DAY:
        if not isinstance(field._property, (DateProperty, DateTimeProperty)):
            raise exceptions.ValidationError(
                'The day operator is only valid against date-based '
                'properties.')
        cypher = ("SUBSTRING(%s, 8, 2) = %s" %
                  (element_name, cypher_primitive(unicode(value).zfill(2))))
    elif condition.operator is OPERATORS.ISNULL:
        if not isinstance(field._property, BoundRelationship):
            cypher = 'HAS(%s)' % re.sub(r'(\?|\!)$', '', element_name)
            if value:
                cypher = 'NOT(%s)' % cypher
    else:
        raise NotImplementedError('Other operators are not yet implemented.')

    return cypher


def cypher_predicates_from_q(q):
    if not isinstance(q, Q):
        identifier = '__'.join(['n'] + q.path)
        if getattr(q.field, 'id', False):
            value_exp = 'ID(%s)' % identifier
        else:
            value_exp = '%s.%s!' % (identifier, q.field.attname)

        # Add an 'HAS()' condition to case unsensitive lookup
        if q.operator in (OPERATORS.IEXACT, OPERATORS.ICONTAINS,
                        OPERATORS.ISTARTSWITH, OPERATORS.IENDSWITH):
            return 'HAS(%s) AND (%s)' % (
                # Remove "!" from value_exp
                value_exp[:-1], 
                cypher_predicate_from_condition(value_exp, q)
            )
        else:
            return '(%s)' % cypher_predicate_from_condition(value_exp, q)
    children = list(not_none(cypher_predicates_from_q(c) for c in q.children))
    if len(children) > 0:
        expr = (" %s " % q.connector).join(children)
        return "NOT (%s)" % expr if q.negated else expr
    return None


def cypher_where_from_q(nodetype, q):
    """
    Build a Cypher WHERE clause based on a str Cypher element identifier that
    should resolve to a node or rel column in the final query, and a Q tree of
    kwarg filters.
    """
    cond_q = condition_tree_from_q(nodetype, q)
    exps = cypher_predicates_from_q(cond_q)
    return "WHERE %s\n" % exps if exps else ''


def cypher_rel_str(rel_type, rel_dir, identifier=None, optional=False):
    dir_strings = ('<-%s-', '-%s->')
    out = neo_constants.RELATIONSHIPS_OUT
    id_str = '`%s`' % identifier if identifier is not None else ''
    return dir_strings[rel_dir == out] % ('[%s%s:`%s`]' %
                                          (id_str, '?' if optional else '', rel_type))


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
                   if not any(other_field.startswith(field)
                              and field != other_field
                              for other_field in fields[i:]))

    for i, field in enumerate(reqd_fields):
        path_name = 'p%d' % i
        returns.append(path_name)

        rel_match_components = []
        cur_m = nodetype
        for step in field.split('__'):
            #try to properly match a model field to the provided field string
            rels = getattr(cur_m._meta, '_relationships', {}).items()
            candidates_on_models = sorted((s for s in ((score_model_rel(step, r), r)
                                          for _, r in rels) if s > 0), reverse=True)
            if len(candidates_on_models) < 1:
                # give up if we can't find a valid candidate
                break
            rel_choice = candidates_on_models[0][-1]
            rel_match_components.append(
                cypher_rel_str(rel_choice.rel_type, rel_choice.direction))
            cur_m = (rel_choice.target_model
                     if not rel_choice.target_model is cur_m
                     else rel_choice.source_model)

        node_match_components = []  # Cypher node identifiers
        type_matches = []  # full Cypher type matching paths for return types
        for ri in xrange(len(rel_match_components)):
            return_node_name = '%s_r%d' % (path_name, ri)
            return_node_type_name = '%s_t' % return_node_name

            returns.extend((return_node_name, '%s.name' % return_node_type_name))

            node_match_components.append(return_node_name)
            type_matches.append('%s-[:`%s`]->%s' %
                                (return_node_type_name, INSTANCE_REL, return_node_name))

        model_match = ''.join(
            itertools.ifilter(None, itertools.chain.from_iterable(
                itertools.izip_longest(rel_match_components,
                                       node_match_components))))

        matches.append('%s=(s%s)' % (path_name, model_match))
        matches.extend(type_matches)

    return 'MATCH %s RETURN %s' % (','.join(matches), ','.join(returns))


def cypher_column_name_from_cond(cond):
    return '__'.join(['n'] + cond.path)


def cypher_match_from_q(nodetype, q):
    # TODO TODO DRY VIOLATION refactor to share common code with
    # select_related and Condition
    paths = []
    conditions = condition_tree_leaves(q)
    for cond in conditions:
        if len(cond.path) > 0:
            path = [NodeComponent('n')]
            cur_m = nodetype
            for level, cond_step in enumerate(cond.path):
                rels = getattr(cur_m._meta, '_relationships', {}).items()
                candidates_on_model = sorted((s for s in (
                    (score_model_rel(cond_step, r), r) for _, r in rels
                ) if s[0] > 0), reverse=True)
                rel_choice = candidates_on_model[0][-1]

                direction = ('>'
                             if (rel_choice.direction == 'out') !=
                                (rel_choice.target_model is nodetype)
                             else '<')
                rel_type = rel_choice.rel_type

                path.append(RelationshipComponent(types=[rel_type],
                        direction=direction, optional=True))

                cur_m = (rel_choice.target_model
                         if not rel_choice.target_model is cur_m
                         else rel_choice.source_model)
                if level != len(cond.path) - 1:
                    path.append(NodeComponent())
            path.append(NodeComponent(cypher_column_name_from_cond(cond)))
            paths.append(path)
    
    return Match(Path(p) for p in paths)


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
        start_expr = u'node(%s)' % ','.join(str(m.id) for m in models)
        start_depth = 1
    elif index_name and query:
        if model_type is None:
            raise ValueError("Must provide a model_type if using select_related"
                             " with an index query.")
        models = []
        start_expr = 'node:`%s`("%s")' % (index_name, str(query).replace('"', '\\"'))
        start_depth = 0
    else:
        raise ValueError("Either a model set or an index name and query need to be provided.")

    conn = connections[using]

    if fields is None:
        if max_depth < 1:
            raise ValueError("If no fields are provided for select_related, max_depth must be > 0.")
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
                   results.get_all_rows(lambda c: re.match('p\d+$', c) is not None)),
                   key=lambda p: p['length'])
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
    rel_nodes_types = ((n, get_model(*t.split(':')))
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
                    rel_on_model._get_or_create_cache()  # set the cache to loaded and empty
                else:
                    #otherwise single side
                    field._set_cached_relationship(m, None)

    #paths ordered by shortest to longest
    paths = sorted(paths, key=lambda v: v['length'])

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
            rel.direction = (neo_constants.RELATIONSHIPS_OUT if node_id == rel.end.id
                             else neo_constants.RELATIONSHIPS_IN)

            field_candidates = [(k, v) for k, v in cur_m._meta._relationships.items()
                                if str(v.rel_type) == str(rel.type) and v.direction == rel.direction]
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
                        key=lambda r: r[0].properties.get(ORDER_ATTR, None))
            else:
                #otherwise single side
                field._set_cached_relationship(cur_m, new_model)
            cur_m = new_model

# we want some methods of sql.Query but don't want the burder of inheriting
# everything. these methods are pulled off django.db.models.sql.query.Query
QUERY_PASSTHROUGH_METHODS = ('set_limits', 'clear_limits', 'can_filter',
                             'add_ordering', 'clear_ordering',
                             'add_distinct_fields', 'add_update_values',
                             'add_update_fields')

@borrows_methods(subqueries.UpdateQuery, QUERY_PASSTHROUGH_METHODS)
class Query(object):
    aggregates_module = aggregates
    query_terms = set([
        'exact', 'contains', 'icontains', 'gt', 'gte', 'lt', 'lte', 'in',
        'startswith', 'istartswith', 'endswith', 'iendswith', 'range', 'year',
        'month', 'day', 'week_day', 'isnull', 'search', 'regex', 'iregex',
        ])

    def __init__(self, model, filters=None, max_depth=None,
                 select_related_fields=[]):
        self.filters = filters or []
        self.model = model
        self.max_depth = max_depth
        self.select_related_fields = list(select_related_fields)
        self.select_related = bool(select_related_fields) or max_depth

        self.return_fields = {'n': 'n'}

        self.aggregates = {}
        self.distinct_fields = []

        self.start_clause = None
        self.start_clause_param_func = lambda: {}
        self.with_clauses = []
        self.end_clause = None

        self.limit_before_return = None

        self.distinct = False
        self.distinct_fields = None

        self.standard_ordering = True
        self.query_terms = None

        # XXX to handle overreaching django code like in the admin - not used
        # otherwise
        self.where = False

        self.clear_limits()
        self.clear_ordering()

        # for updates
        self.values = []
        self.related_updates = {}

    def add_q(self, q):
        cond_q = condition_tree_from_q(self.model, q)
        self.filters.append(cond_q)
        return self

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
        except:  # TODO fix bare except (FieldDoesNotExist)
            source = field_alias

        aggregate.add_to_query(self, alias, col=field_alias, source=source,
                               is_summary=is_summary)

    def add_related_update(self, model, field, value):
        raise FieldError('Cannot update model field %s - only non-relations are'
                         ' permitted.' % field)

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
        self.start_clause_param_func = (param_dict_or_func if
                                        callable(param_dict_or_func) else lambda: param_dict_or_func)

    def get_start_clause_and_param_dict(self):
        # TODO if a clause has been set, return that
        # otherwise, compute it from conditions
        # (requires a refactor from as_groovy())
        return self.start_clause, self.start_clause_param_func()

    def set_limit_before_return(self, i):
        self.limit_before_return = i

    def model_from_node(self, node):
        return self.model._neo4j_instance(node)

    def clone(self):
        clone = type(self)(self.model, self.filters, self.max_depth,
                           self.select_related_fields)
        clone_attrs = ('order_by', 'return_fields', 'aggregates', 'distinct',
                       'distinct_fields', 'high_mark', 'low_mark',
                       'start_clause', 'start_clause_param_func',
                       'with_clauses', 'end_clause', 'standard_ordering',
                       'limit_before_return', 'values', 'related_updates')
        for a in clone_attrs:
            setattr(clone, a, getattr(self, a))
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
        return {query.return_fields.keys()[0]: result_set[0]}

    def as_groovy(self, using):
        filters = uniqify(self.filters)

        id_conditions = []

        # check all top-level children for id conditions
        for q in filters:
            if q.connector == 'AND':
                id_conditions.extend(c for c in q.children
                                     if getattr(getattr(c, 'field', False),
                                                'id', False))

        grouped_id_conds = itertools.groupby(id_conditions, lambda c: c.operator)
        id_lookups = dict(((k, list(l)) for k, l in grouped_id_conds))

        exact_id_lookups = list(id_lookups.get(OPERATORS.EXACT, []))
        if len(exact_id_lookups) > 1:
            raise ValueError("Conflicting exact id lookups - a node can't have"
                             " two ids.")

        in_id_lookups = list(id_lookups.get(OPERATORS.IN, []))

        # build index queries from filters

        index_qs = not_none(lucene_query_and_index_from_q(using, self.model, q)
                            for q in filters)
        
        # combine any queries headed for the same index and replace lucene
        # queries with strings

        index_qs_dict = {}
        for key, val in index_qs:
            if key in index_qs_dict:
                if index_qs_dict[key]:
                    index_qs_dict[key] &= val
            else:
                index_qs_dict[key] = val

        index_qs = [(key, unicode(val)) for key, val in index_qs_dict.iteritems()
                    if val is not None]
        
        # use index lookups, ids, OR a type tree traversal as a cypher START,
        # then unindexed conditions as a WHERE

        start_clause, cypher_params = self.get_start_clause_and_param_dict()

        # add the typeNodeId param, either for type verification or initial
        # type tree traversal
        cypher_params['typeNodeId'] = self.model._type_node(using).id

        type_restriction_expr = """
        n<-[:`<<INSTANCE>>`]-()<-[:`<<TYPE>>`*0..]-typeNode
        """

        type_restriction_pattern = Path([
            NodeComponent('n'),
            RelationshipComponent(types=['<<INSTANCE>>'], direction='<'),
            NodeComponent(),
            RelationshipComponent(types=['<<TYPE>>'], direction='<',
                                  length_or_range=(0, None)),
            NodeComponent('typeNode'),
        ])

        # separate filters into those requiring a MATCH clause and those that
        # don't
        non_spanning_filters = []
        spanning_filters = []

        for q in filters:
            if all((len(cond.path) < 1) for cond in condition_tree_leaves(q)):
                non_spanning_filters.append(q)
            else:
                spanning_filters.append(q)

        where_clause = cypher_where_from_q(self.model,
                                           Q(*non_spanning_filters))

        with_clauses = self.with_clauses or []

        limit = self.high_mark - self.low_mark if self.high_mark is not None else None

        if self.end_clause is None and len(self.values) > 0:
            # for updating queries
            return_clause = Clauses([Set(dict((tup[0].name, tup[2])
                                              for tup in self.values)),
                                     Return(self.return_fields)])
        elif self.end_clause is None:
            return_clause = Return(self.return_fields, skip=self.low_mark,
                    limit=limit, distinct_fields=['n'] if self.distinct else [])
        else:
            return_clause = self.end_clause

        order_by = OrderBy([OrderByTerm(ColumnExpression('n', field.lstrip('-')),
                                        negate=(field.startswith('-') == self.standard_ordering))
                            for field in self.order_by]) if self.order_by else None

        if order_by is not None:
            # decide where to inject the ORDER BY expression - if the fields
            # ordered aren't being returned, it needs to go before the RETURN
        
            all_order_ids_returned = all(i in return_clause.passing_identifiers
                                         for i in order_by.required_identifiers)
            if isinstance(return_clause, Return) and all_order_ids_returned:
                return_clause.order_by = order_by
            else:
                # TODO if the clauses before this don't have all the proper
                # passing identifiers, raise an exception
                prior_clause = ([start_clause] + with_clauses)[-1]
                passing_ids = getattr(
                    prior_clause, 'passing_identifiers', ['n'])
                with_clauses.append(With(dict((i, i) for i in passing_ids),
                                        order_by=order_by))

        groovy_script = None
        params = {
            #TODO HACK need a generalization
            'returnColumn': self.return_fields.keys()[0]
        }

        # TODO none of these queries but the last properly take type into
        # account.
        if len(in_id_lookups) > 0 or len(exact_id_lookups) > 0:
            # collect id lookups by column
            in_id_lookups_by_column = defaultdict(list)
            exact_id_lookups_by_column = defaultdict(list)

            for lookup in in_id_lookups:
                in_id_lookups_by_column[cypher_column_name_from_cond(lookup)]\
                        .append(lookup)

            for lookup in exact_id_lookups:
                exact_id_lookups_by_column[cypher_column_name_from_cond(lookup)]\
                        .append(lookup)

            columns = uniqify(exact_id_lookups_by_column.keys() + 
                              in_id_lookups_by_column.keys())

            start_field_dict = {}
            start_param_dict = {}

            # for each column, check for conflicts between id lookups and build
            # out the start clause field dict
            for column in columns:
                exact_lookups = exact_id_lookups_by_column[column]
                in_lookups = in_id_lookups_by_column[column]
                id_set = reduce(and_, (set(c.value) for c in in_lookups)) if in_lookups else set([])
                if len(exact_lookups) > 0:
                    exact_ids = uniqify(e.value for e in exact_lookups)
                    if len(exact_ids) > 1:
                        raise ValueError("Conflicting id__exact lookups - a "
                                         "node can't have two ids.")
                    exact_id = exact_ids[0]
                    if id_set and exact_id not in id_set:
                        raise ValueError("Conflicting id__exact and id__in lookups"
                                         " - a node can't have two ids.")
                    else:
                        id_set = set([exact_id])

                if len(id_set) >= 1:
                    param = column + '_startParam'
                    start_field_dict[column] = 'node({%s})' % param
                    start_param_dict[param] = list(id_set)

            if len(start_field_dict) >= 1:
                start_clause = start_clause or Start(start_field_dict,
                                                     start_param_dict.keys())
                groovy_script = """
                    results = []
                    startParams = startParams.findResults{
                        if (it.value) {
                            [it.key, Neo4Django.getVerticesByIds(it.value).collect{v -> v.id}]
                        }
                    }.collectEntries()
                    cypherParams += startParams
                    table = Neo4Django.cypher(cypherQuery,cypherParams)
                    results = table.columnAs(returnColumn)
                    """
                params['startParams'] = start_param_dict
            else:
                # XXX None is returned, meaning an empty result set
                return (None, None)
        elif len(index_qs) > 0:
            start_clause = start_clause or Start({'n': 'node({startParam})'},
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
            #TODO move this to being index-based - it won't work for abstract model queries
            if start_clause is None:
                start_clause = Clauses([Start({'typeNode': 'node({typeNodeId})'},
                                              ['typeNodeId']),
                                        Match([type_restriction_pattern])])
                # we don't need an additional type restriction since it's
                # handled in the start
                type_restriction_pattern = None
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

        # add groovy to re-index after an update
        if len(self.values) > 0:
            reindex_values = [((model or self.model).index_name(),
                               field.name,
                               field.to_neo_index(value))
                              for field, model, value in self.values if field.indexed]
            if len(reindex_values) > 0:
                groovy_script += """
                def nodeToIndex, rawIndex, index
                while( results.hasNext() ) {
                    nodeToIndex = results.next()
                    valuesToIndexPerNode.each{ indexName, field, value ->
                        (index, rawIndex) = Neo4Django.getOrCreateIndex(indexName)
                        rawIndex.add(nodeToIndex, field, value)
                    }
                }
                """
                params['valuesToIndexPerNode'] = reindex_values


        # take care of any relationship-spanning lookups
        if len(spanning_filters) > 0:
            combined_filter = reduce(and_, spanning_filters)
            # build match clause
            match = cypher_match_from_q(self.model, combined_filter)
            # build where clause
            where = cypher_where_from_q(self.model, combined_filter)
            # TODO DRY VIOLATION this prior_clause / WITH clause pattern is showing up a alot
            prior_clause = ([start_clause] + with_clauses)[-1]
            passing_ids = getattr(
                prior_clause, 'passing_identifiers', ['n'])
            with_clauses.append(With(dict((i, i) for i in passing_ids),
                                     match=match, where=where))

        # if the type restriction hasn't been removed and this isn't an
        # abstract model (which can't be type restricted as easily)
        if type_restriction_pattern and not getattr(self.model._meta,
                                                    'abstract', False):
            # TODO DRY violation
            prior_complex_clause = ([start_clause] + with_clauses)[-1]
            passing_ids = getattr(prior_complex_clause, 'passing_identifiers', ['n'])
            with_clauses.append(With(dict((i, i) for i in passing_ids),
                    where='WHERE ' + unicode(type_restriction_pattern)))

        if self.limit_before_return:
            # TODO DRY violation
            prior_clause = ([start_clause] + with_clauses)[-1]
            passing_ids = getattr(
                prior_clause, 'passing_identifiers', ['n'])
            with_clauses.append(With(dict((i, i) for i in passing_ids),
                                    limit=self.limit_before_return))



        str_clauses = [start_clause.as_cypher(), where_clause] + \
                      [c.as_cypher() for c in with_clauses] + \
                      [return_clause.as_cypher()]

        params['cypherQuery'] = ' '.join(str_clauses) + ';'
        params['cypherParams'] = cypher_params

        return groovy_script, params

    def execute(self, using):
        conn = connections[using]

        groovy, params = self.as_groovy(using)

        raw_result_set = conn.gremlin_tx(groovy, **params) if groovy is not None else []

        #make the result_set not insane (properly lazy)
        result_set = [add_auth(LazyNode.from_dict(d), conn)
                      for d in raw_result_set._list] if raw_result_set else []

        model_results = [self.model_from_node(n) for n in result_set]

        if self.select_related:
            sel_fields = self.select_related_fields
            if not sel_fields:
                sel_fields = None
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

    def update(self, using, updates):
        if 'id' in updates or 'pk' in updates:
            raise FieldError("Neo4j doesn't allow node ids to be updated.")
        clone = self.clone()
        clone.add_update_values(updates)
        for m in clone.execute(using):
            pass

    def get_count(self, using):
        from django.db.models import Count
        obj = self.clone()
        obj.add_aggregate(Count('*'), self.model, 'count', True)
        aggregation = obj.get_aggregation(using)
        return aggregation.get('count', None)

    def has_results(self, using):
        obj = self.clone()
        obj.clear_ordering(True)
        obj.set_limit_before_return(1)
        return obj.get_count(using) > 0


#############
# QUERYSETS #
#############

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
                    i += 1
                except (IndexError, StopIteration):
                    return False
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

    #TODO leaving this todo for later transaction work
    @transactional
    def create(self, **kwargs):
        if 'id' in kwargs or 'pk' in kwargs:
            raise FieldError("Neo4j doesn't allow node ids to be assigned.")
        return super(NodeQuerySet, self).create(**kwargs)

    #TODO would be awesome if this were transactional
    def get_or_create(self, **kwargs):
        defaults = kwargs.pop('defaults', {})
        try:
            obj = self.get(**kwargs)
            created = False
        except:
            values = dict(defaults)
            values.update(kwargs)
            obj = self.create(**values)
            created = True
        return (obj, created)

    @transactional
    def in_bulk(self, id_list):
        return dict((o.id, o) for o in self.model.objects.filter(id__in=id_list))

    @alters_data
    def delete(self):
        self.query.delete(self.db)

    @alters_data
    def update(self, **kwargs):
        self.query.update(self.db, kwargs)

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

    def distinct(self, *field_names):
        if len(field_names) > 0:
            raise NotImplementedError("Only querying against distinct nodes is "
                                      "implemented. Distinct fields cannot be "
                                      "queried against.")
        return super(NodeQuerySet, self).distinct(*field_names)

    @not_supported
    def extra(self, *args, **kwargs):
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
        c = super(NodeDateQuerySet, self)._clone(klass, False, **kwargs)
        c._field_name = self._field_name
        c._kind = self._kind
        if setup and hasattr(c, '_setup_query'):
            c._setup_query()
        return c
