class Aggregate(object):
    """
    Default Cypher Aggregate.
    """
    cypher_template = '%(function)s(%(field)s)'

    def __init__(self, prop_name, source=None, is_summary=False, **extra):
        """
        Instantiate a Cypher aggregate. Uses similar class attributes to
        django.db.models.sql.aggregates.Aggregate.
        """

        self.prop_name = prop_name
        self.source = source
        self.is_summary = is_summary
        self.extra = extra

        self.field = source

    def as_cypher(self):
        "Return the aggregate, rendered as SQL."

        field_name = self.prop_name

        params = {
            'function': self.cypher_function,
            'field': field_name
        }
        params.update(self.extra)

        return self.cypher_template % params


class Avg(Aggregate):
    cypher_function = 'AVG'

class Count(Aggregate):
    cypher_function = 'COUNT'
    cypher_template = '%(function)s(%(distinct)s%(field)s)'

    def __init__(self, col, distinct=False, **extra):
        super(Count, self).__init__(col, distinct=distinct and 'DISTINCT ' or '', **extra)

class Max(Aggregate):
    cypher_function = 'MAX'

class Min(Aggregate):
    cypher_function = 'MIN'

class Sum(Aggregate):
    cypher_function = 'SUM'

