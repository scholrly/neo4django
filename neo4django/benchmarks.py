from inspect import isfunction
from time import time
from db import models

import requests

####################
# BENCHMARK MODELS #
####################

class SimpleModel(models.NodeModel):
    class Meta:
        app_label = 'benchmark'
    age = models.IntegerProperty()
    name = models.StringProperty()

class IndexedModel(models.NodeModel):
    class Meta:
        app_label = 'benchmark'
    age = models.IntegerProperty(indexed=True)
    name = models.StringProperty(indexed=True)

class Parent(models.NodeModel):
    class Meta:
        app_label = 'benchmark'
    name = models.StringProperty()

class Child(models.NodeModel):
    class Meta:
        app_label = 'benchmark'
    name = models.StringProperty()
    age = models.IntegerProperty()
    parents = models.Relationship(Parent, 'CHILD_OF')

class Employer(models.NodeModel):
    class Meta:
        app_label = 'benchmark'
    name = models.StringProperty()
    employees = models.Relationship(Parent, 'EMPLOYS')

##############
# BENCHMARKS #
##############

def simple_creation_benchmark():
    for i in xrange(100):
        SimpleModel.objects.create(name=str(i), age=i)

def indexed_creation_benchmark():
    for i in xrange(100):
        IndexedModel.objects.create(name=str(i), age=i)

def related_creation_benchmark():
    for i in xrange(100):
        employer = Employer(name=str(i))
        employees = [Parent(name=str(x)) for x in xrange(5)]
        for employee in employees:
            employee.children = [Child(name=str(x)) for x in xrange(2)]
        employer.employees = employees
        employer.save()

def get_names_benchmark():
    parents = Parent.objects.all()
    [p.name for p in parents]

def get_related_benchmark():
    employers = Employer.objects.all()
    for e in employers:
        for p in e.employees.all():
            p.name

################
# BENCHMARKING #
################

from django.conf import settings

def cleandb():
    host = settings.NEO4J_DATABASES['default']['HOST']
    port = settings.NEO4J_DATABASES['default']['PORT']
    key = getattr(settings, 'NEO4J_DELETE_KEY', None)
    if key:
        requests.delete('http://%s:%s/cleandb/%s' % (host, port, key))

cleandb()

benchmarks = (f for f in locals().items() 
              if isfunction(f[1]) and f[0].endswith('_benchmark'))
for b in benchmarks:
    #yes, we're using time() for now, since it's io-bound it makes sense
    start = time()
    b[1]()
    end = time()

    print "'%s':%.3f" % (b[0][:-10],end-start)

cleandb()
