import neo4jrestclient.client as neo4j
import neo4jrestclient.constants as neo_constants

from neo4django.db import DEFAULT_DB_ALIAS
from neo4django.utils import Enum, uniqify
from neo4django.decorators import transactional, not_supported, alters_data, \
        not_implemented

from django.db.models.query import QuerySet
from django.core import exceptions

from lucenequerybuilder import Q
from jexp import J

from collections import namedtuple
from operator import itemgetter as getter, and_
from itertools import groupby
import re

#python needs a bijective map... grumble... but a reg enum is fine I guess
#only including those operators currently being implemented
OPERATORS = Enum('EXACT', 'LT','LTE','GT','GTE','IN','RANGE')

Condition = namedtuple('Condition', ['field','value','operator','negate'])

QUERY_CHUNK_SIZE = 10

def matches_condition(node, condition):
    """
    Return whether a node matches a filtering condition.
    """
    field, val, op, neg = condition
    passed = False
    if node.get(field.attname, None):
        att = node[field.attname]
        passed = (op is OPERATORS.EXACT and att == val) or \
                 (op is OPERATORS.RANGE and val[0] < att < val[1]) or \
                 (op is OPERATORS.LT and att < val) or \
                 (op is OPERATORS.LTE and att <= val) or \
                 (op is OPERATORS.GT and att > val) or \
                 (op is OPERATORS.GTE and att >= val) or \
                 (op is OPERATORS.IN and att in val)
    if neg:
        passed = not passed
    return passed

def is_of_types(node, ts):
    #TODO return true if the provided node is of type t, or of a subtype of t
    pass

def q_from_condition(condition):
    """
    Build a Lucene query from a filtering condition.
    """
    q = None
    field = condition.field
    attname = field.attname
    if condition.operator is OPERATORS.EXACT:
        q = Q(attname, field.to_neo_index(condition.value))
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
    #XXX assumption that attname is how the prop is stored
    has_prop = end_node.hasProperty(name)
    get_prop = end_node.getProperty(name)
    if op == OPERATORS.EXACT:
        correct_val = get_prop == val
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
    else:
        raise NotImplementedError('Other operators are not yet implemented.')
        #TODO implement other operators
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

class Query(object):
    def __init__(self, nodetype, conditions=tuple()):
        self.conditions = conditions
        self.nodetype = nodetype
    
    def add(self, prop, value, operator=OPERATORS.EXACT, negate=False):
        #TODO validate, based on prop type, etc
        return type(self)(self.nodetype, conditions = self.conditions +\
                          (Condition(prop, value, operator, negate),))

    def add_cond(self, cond):
        return type(self)(self.nodetype, conditions = self.conditions +\
                          tuple([cond]))

    def can_filter(self):
        return False #TODO not sure how we should handle this

    #TODO optimize query by using type info, instead of just returning the
    #proper type
    #TODO when does a returned index query of len 0 mean we're done?
    def execute(self, using, optimize=False): #TODO do ssomething with optimize
        first = getter(0)
        conditions = uniqify(self.conditions)
        #gather all indexed fields
        #TODO exclude those that can't be evaluated against (eg exact=None, etc)
        indexed = [c for c in conditions if first(c).indexed]
        unindexed = [c for c in conditions if c not in indexed]

        results = {} #TODO this could perhaps be cached - think about it
        filtered_results = set()

        #TODO order by type - check against the global type first, so that if
        #we get an empty result set, we can return none

        for index, group in groupby(indexed, lambda c:c.field.index(using)):
            q = None
            for condition in group:
                new_q = q_from_condition(condition)
                if not new_q:
                    break
                if q:
                    q &= new_q
                else:
                    q = new_q
            result_set = set(index.query(q))
            #TODO results is currently worthless
            results[q] = result_set
            #TODO also needs to match at least one type, if any have been provided
            #filter for unindexed conditions, as well
            filtered_result = set(n for n in result_set \
                               if all(matches_condition(n, c) for c in unindexed)\
                                  and n not in filtered_results)
            filtered_results |= filtered_result
            for r in filtered_result:
                yield r

        if not indexed:
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
                for r in filtered_result:
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
        return type(self)(self.nodetype, self.conditions)

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
    
    if op is OPERATORS.RANGE:
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
        for node in self.query.execute(using):
            yield self.model._neo4j_instance(node)

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

    def get_or_create(self, **kwargs):
        try:
            return self.get(**kwargs)
        except ValueError:
            return self.create(**kwargs)

    @not_implemented
    def latest(self, field_name=None):
        pass

    @not_implemented
    def in_bulk(self, id_list):
        pass
    
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

    @not_implemented
    def select_related(self, *fields, **kwargs):
        pass

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
    #using gremlin and some other magin in query, we might be able to swing
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

