from django.db import models
from django.db.models.query import EmptyQuerySet

from neo4django.decorators import not_implemented
from query import NodeQuerySet

class NodeModelManager(models.Manager):
    def __init__(self):
        super(NodeModelManager, self).__init__()
        self._using = None
        self.model = None
        self._inherited = False

    @not_implemented
    def _insert(self, values, **kwargs):
        pass
    
    @not_implemented
    def _update(self, values, **kwargs):
        pass   

    def get_empty_query_set(self):
        return EmptyQuerySet()

    @not_implemented
    def exclude(self, *args, **kwargs):
        pass

    def get_query_set(self):
        return NodeQuerySet(self.model)

    def all(self):
        return self.get_query_set()

    def get(self, *args, **kwargs):
        return self.get_query_set().get(*args, **kwargs)

    def dates(self, *args, **kwargs):
        return self.get_query_set().dates(*args, **kwargs)
    
    def create(self, **kwargs):
        return self.get_query_set().create(**kwargs)
    
    def filter(self, *args, **kwargs):
        if args:
            raise NotImplementedError('The Q operator is not currently supported')
        return self.get_query_set().filter(**kwargs)
