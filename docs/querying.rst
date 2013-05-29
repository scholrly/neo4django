========
Querying
========

Querying should be easy for anyone familiar with Django. Model managers return
a subclass of :class:`~django.db.models.query.QuerySet` that converts queries
into the `Cypher <http://docs.neo4j.org/chunked/milestone/cypher-query-lang.html>`_ 
graph query language, which yield :class:`~neo4django.db.models.NodeModel`
instances on execution.

Most of the `Django QuerySet API <https://docs.djangoproject.com/en/1.4/ref/models/querysets/>`_
is implemented, with exceptions noted in the `project issues <https://github.com/scholrly/neo4django/issues>`_. We've added two field lookups- `member` and `member_in`- to make searching over array properties easier. For an 
``OnlinePerson`` instance with an ``emails`` property, query against the field
like::

    OnlinePerson.objects.filter(emails__member="wicked_cool_email@example.com")

=====
JOINs
=====

It's important to remember that, since we're using a graph database, "JOIN-like"
operations are much less expensive. Consider a more connected model::

    class FamilyPerson(Person):
        parents = Relationship('self', rel_type='child_of')
        stepdad = Relationship('self', rel_type='step_child_of', single=True)
        siblings = Relationship('self', rel_type='sibling_of')
        # hopefully this is one-to-one...
        spouse = Relationship('self', rel_type='married_to', single=True, rel_single=True)

Finding a child with parents named Tom and Meagan and a stepdad named Jack is simple::

    FamilyPerson.objects.filter(parents__name__in=['Tom','Meagan']).filter(stepdad__name='Jack')

If we'd like to pre-load a subgraph around a particular ``FamilyPerson``, we can
use :func:`~neo4django.db.models.query.NodeQuerySet.select_related`::

    jack = Person.objects.filter(name='Jack').select_related(depth=5)
    #OR
    Person.objects.get(name='Jack').select_related('spouse__mother__sister__son__stepdad')

...either of which will pre-load Jack's extended family so he can go about
recalling names without hitting the database a million times. 

