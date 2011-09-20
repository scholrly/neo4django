from django.db import models

from neo4django.decorators import not_implemented
from query import NodeQuerySet

class NodeModelManager(models.Manager):
    def __init__(self):
        self._using = None
        self.model = None

    @not_implemented
    def _insert(self, values, **kwargs):
        pass
    
    @not_implemented
    def _update(self, values, **kwargs):
        pass   

    @not_implemented
    def get_empty_query_set(self):
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
        return self.get_query_set().filter(**kwargs)
