from inspect import isfunction
from time import time
from db import models

def simple_creation_benchmark():
    class SimpleModel(models.NodeModel):
        class Meta:
            app_label = 'benchmark'
        age = models.IntegerProperty()
        name = models.StringProperty()
    for i in xrange(100):
        SimpleModel.objects.create(name=str(i), age=i)

def indexed_creation_benchmark():
    class IndexedModel(models.NodeModel):
        class Meta:
            app_label = 'benchmark'
        age = models.IntegerProperty(indexed=True)
        name = models.StringProperty(indexed=True)
    for i in xrange(100):
        IndexedModel.objects.create(name=str(i), age=i)

def related_creation_benchmark():
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

    for i in xrange(100):
        class Meta:
            app_label = 'benchmark'
        employer = Employer(name=str(i))
        employees = [Parent(name=str(x)) for x in xrange(5)]
        for employee in employees:
            employee.children = [Child(name=str(x)) for x in xrange(2)]
        employer.employees = employees
        employer.save()


################
# BENCHMARKING #
################

benchmarks = (f for f in locals().items() 
              if isfunction(f[1]) and f[0].endswith('_benchmark'))
for b in benchmarks:
    #yes, we're using time() for now
    start = time()
    b[1]()
    end = time()

    print "'%s':%d" % (b[0][:-10],end-start)
