import itertools
from collections import Iterable

from django.core import exceptions

from ...utils import not_none, uniqify


def cypher_primitive(val):
    if isinstance(val, basestring):
        return '"%s"' % val
    elif val is None:
        return 'null'
    elif isinstance(val, Iterable):
        return "[%s]" % ','.join(cypher_primitive(v) for v in val)
    return str(val)


def cypher_escape_identifier(i):
    return u'`%s`' % i


####################
# QUERY COMPONENTS #
####################

class Cypher(object):
    """
    A class representing a Cypher snippet. Subclasses should implement
    get_params() and have an attribute, 'cypher_template', which will be used
    as a %-style template with the dict returned from get_params().

    Alternatively, subclasses can override as_cypher(), returning a unicode
    query string, for more complicated situations.
    """

    def as_cypher(self):
        return self.cypher_template % self.get_params()

    def __unicode__(self):
        return self.as_cypher()

    @property
    def required_identifiers(self):
        """
        A list of str identifiers required by this Cypher snippet.
        """
        return []

    @property
    def passing_identifiers(self):
        """
        A list of identifiers yielded by this Cypher snippet.
        """
        return []


class NodeComponent(Cypher):
    """ A paren-delimited node identifier, for use in path expressions."""

    cypher_template = '%(id)s'

    def __init__(self, identifier=None):
        self.identifier = identifier

    def get_params(self):
        ident = self.identifier
        return {
            'id':cypher_escape_identifier(ident) if ident else ''
        }

    @property
    def passing_identifiers(self):
        return list(not_none([self.identifier]))


class RelationshipComponent(Cypher):
    """ A relationship component for use in path expressions."""

    cypher_template = '-[%(id)s%(optional)s%(types)s%(length_range)s]-'
    
    def __init__(self, identifier=None, types=[], optional=False,
                 length_or_range=1, direction='>'):
        """
        Arguments:
        identifier - the relationship identifier, if needed
        types - a list of string relationship types this component can match,
        if needed
        optional - whether or not the relationship is optional (defaults to False)
        length_or_range - an integer represention a length (defaults to 1) or a
        tuple pair of ranges for variable-length relationships. None at
        either end means an unbound range (eg, `(5, None)` with yield a `5..`
        range.
        direction - a '>' or '<' indicating which direction the relationship
        string should point
        """
        if isinstance(length_or_range, Iterable) and len(length_or_range) != 2:
            raise ValueError("length_or_range should be an integer or an "
                             "integer pair.")
        self.identifier = identifier
        self.types = types
        self.length_or_range = length_or_range
        self.direction = direction
        self.optional = optional

    def get_params(self):
        length = self.length_or_range
        length_range = unicode(length) if isinstance(length, int) else \
                u'%s..%s' % tuple('' if i is None else unicode(i) for i in length)
        return {
            'id':self.identifier or '',
            'types':u':' + u'|'.join(cypher_escape_identifier(t)
                                     for t in self.types) \
                    if len(self.types) > 0 else '',
            'length_range':u'*' +  length_range if length != 1 else '',
            'optional':'?' if self.optional else ''
        }

    def as_cypher(self):
        sup = super(RelationshipComponent, self).as_cypher()
        if self.direction == '<':
            return '<' + sup
        return sup + '>'

    @property
    def passing_identifiers(self):
        return list(not_none([self.identifier]))


class Path(Cypher):
    """ A path expression, like those used in MATCH and WHERE clauses."""

    cypher_template = '%(path_assignment)s%(path_expr)s'

    # TODO there should be a way to specify which ids are already bound, and
    # which aren't so we can get a proper 'required_identifiers' list
    def __init__(self, components, path_variable=None,
                 required_identifiers=tuple()):
        """
        Arguments:
        components - a list of alternating node and relationship components

        Keyword arguments:
        path_variable - a string path identifer. If included, the final Cypher
        output will be a named path (eg, "p=(`n`)-[:`friends_with`]->(`m`)").
        Note that path_variable shouldn't be included for paths meant to be used
        is a WHERE clause.
        required_identifiers - a list of identifiers this path requires to be
        connected, if any (eg, `['n']` for a path the expects the column 'n'
        to have been bound earlier in the query).
        """
        self.path_variable = path_variable
        if len(components) % 2 == 0:
            raise exceptions.ValidationError('Paths must have an odd number of '
                                             'components.')
        self.components = components
        self._required_identifiers = required_identifiers

    def get_params(self):
        components = self.components[:]
        components.append(None)  # make the list even-length
        #break components into pairs and fix the node identifiers
        pairs = [('(%s)' % unicode(p[0]) if p[0] else '()', p[1])
                 for p in zip(*[iter(components)] * 2)]
        components = list(itertools.chain.from_iterable(pairs))[:-1]
        
        return {
            'path_assignment': '%s =' % cypher_escape_identifier(self.path_variable)
                               if self.path_variable is not None else '',
            'path_expr': u''.join(unicode(c) for c in components)
        }

    @property
    def passing_identifiers(self):
        return uniqify(not_none([self.path_variable] + \
                list(itertools.chain.from_iterable(c.passing_identifiers
                                                   for c in self.components))))

    @property
    def required_identifiers(self):
        return self._required_identifiers


class ColumnExpression(Cypher):
    """
    A column expression to be used in WHERE comparisons or RETURN lists.
    """

    def __init__(self, column, prop=None, fail_on_missing=False):
        self.column = column
        self.prop = prop
        self.fail_on_missing = fail_on_missing

    def as_cypher(self):
        expr =  u'.'.join(cypher_escape_identifier(i)
                          for i in not_none((self.column, self.prop)))
        return expr + ('!' if self.fail_on_missing else '?')

    @property
    def required_identifiers(self):
        return not_none([self.column])


class OrderByTerm(Cypher):
    """ An expression used in an ORDER BY clause."""

    cypher_template = '%(expr)s %(desc)s'

    def __init__(self, expression, negate=False):
        self.expression = expression
        self.negate = negate

    def get_params(self):
        return {
            'expr': unicode(self.expression),
            'desc':'DESC' if self.negate else ''
        }
    
    @property
    def required_identifiers(self):
        return getattr(self.expression, 'required_identifiers', [])


###########
# CLAUSES #
###########

class Clause(Cypher):
    @property
    def passing_identifiers(self):
        return []


class Clauses(list):
    def as_cypher(self):
        return u' '.join(unicode(c) for c in self)

    @property
    def passing_identifiers(self):
        return self[-1].passing_identifiers if len(self) > 0 \
                and hasattr(self[-1], 'passing_identifiers') else []

    @property
    def required_identifiers(self):
        return self[0].required_identifiers if len(self) > 0 \
                and hasattr(self[0], 'required_identifiers') else []


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
            'exprs': ','.join('%s=%s' % (k, v)
                              for k, v in self.start_assignments.iteritems())
        }

    @property
    def passing_identifiers(self):
        return self.start_assignments.keys()


class Match(Clause):
    cypher_template = 'MATCH %(exprs)s'

    def __init__(self, paths):
        """
        paths - a list of strs of objects with as_cypher() methods that return
        Cypher paths- eg, "n-[:FRIENDS_WITH]->friend" or "path=n-->out".
        """
        paths = list(paths)
        if len(paths) < 1:
            raise exceptions.ValidationError('MATCH clauses require at least '
                                             'one path.')
        self.paths = paths

    def get_params(self):
        return {
            'exprs': u','.join(unicode(p) for p in self.paths)
        }

    @property
    def passing_identifiers(self):
        return uniqify(itertools.chain.from_iterable(
            p.passing_identifiers if hasattr(p, 'passing_identifiers') else []
            for p in self.paths))
    

class With(Clause):
    cypher_template = 'WITH %(fields)s %(order_by)s %(limit)s %(match)s %(where)s'

    def __init__(self, field_dict, order_by=None, limit=None, where=None, match=None):
        self.field_dict = field_dict
        self.order_by = order_by
        self.limit = limit
        self.where = where
        self.match = match

    def get_params(self):
        return {
            'fields': ','.join('%s AS %s' % (alias, field)
                               for alias, field in self.field_dict.iteritems()),
            'order_by':unicode(self.order_by) if self.order_by else '',
            'limit': 'LIMIT %s' % str(self.limit)
                     if self.limit is not None else '',
            'match': ((self.match.as_cypher() if hasattr(self.match, 'as_cypher')
                       else unicode(self.match)) if self.match else ''),
            'where': ((self.where.as_cypher() if hasattr(self.where, 'as_cypher')
                       else unicode(self.where)) if self.where else ''),
        }
    
    @property
    def passing_identifiers(self):
        match_ids = self.match.passing_identifiers \
                if hasattr(self.match, 'passing_identifiers') else []
        return uniqify(self.field_dict.keys() + match_ids)


class OrderBy(Clause):
    cypher_template = 'ORDER BY %(fields)s'

    def __init__(self, terms):
        self.terms = terms
    
    def get_params(self):
        return {
            'fields':u','.join(unicode(t) for t in self.terms)
        }

    @property
    def required_identifiers(self):
        return uniqify(itertools.chain.from_iterable(
            getattr(t, 'required_identifiers', []) for t in self.terms))


class Return(Clause):
    cypher_template = 'RETURN %(fields)s %(order_by)s %(skip)s %(limit)s'

    def __init__(self, field_dict, limit=None, skip=None, order_by=None,
                 distinct_fields=None):
        self.field_dict = field_dict
        self.limit = limit
        self.skip = skip
        self.order_by = order_by
        self.distinct_fields = distinct_fields

    def get_params(self):
        distinct_fields = set(self.distinct_fields or [])
        field_alias_pairs = ((field if field not in distinct_fields
                              else 'DISTINCT ' + field, alias)
                             for alias, field in self.field_dict.iteritems())
        return {
            'fields': ','.join('%s AS %s' % pair for pair in field_alias_pairs),
            'limit': 'LIMIT %s' % str(self.limit)
                     if self.limit is not None else '',
            'skip': 'SKIP %d' % self.skip if self.skip else '',
            'order_by': '' if self.order_by is None else unicode(self.order_by)
        }

    @property
    def passing_identifiers(self):
        return self.field_dict.keys()

    @property
    def required_identifiers(self):
        # TODO ultimately we want to know what's required for the field dict as
        # well, but we need more structure for that
        return getattr(self.order_by, 'required_identifiers', []) \
                if self.order_by is not None else []


class Delete(Clause):
    cypher_template = 'DELETE %(fields)s'

    def __init__(self, fields):
        self.fields = fields

    def get_params(self):
        return {
            'fields': ','.join(cypher_escape_identifier(f) for f in self.fields)
        }

    @property
    def required_identifiers(self):
        # TODO ultimately we want to know what's required for the field dict as
        # well, but we need more structure for that
        return list(self.fields)


class DeleteNode(Delete):
    cypher_template = 'WITH %(fields)s MATCH %(field_matches)s DELETE %(fields_and_rels)s'

    def get_params(self):
        field_rels = ['%s_r' % f for f in self.fields]
        params = {
            'fields': ','.join(self.fields),
            'fields_and_rels': ','.join(self.fields + field_rels),
            'field_matches': ','.join('(%s)-[%s]-()' % (f, r)
                             for f, r in zip(self.fields, field_rels))
        }
        return params


class Set(Clause):
    cypher_template = 'SET %(fields)s'

    def __init__(self, fields_and_values):
        self.fields_and_values = fields_and_values

    def get_params(self):
        assignments = ('n.%s=%s' % (cypher_escape_identifier(field),
                                  cypher_primitive(value))
                       for field, value in self.fields_and_values.iteritems())
        params = {
            'fields':','.join(assignments),
        }
        return params

    # TODO
    #@property
    #def required_identifiers(self)
