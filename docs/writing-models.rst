==============
Writing Models
==============

Models look similar to typical Django models. A neo4django model definition
\might look like this::


    from neo4django.db import models

    class Person(models.NodeModel):
        name = models.StringProperty()
        age = models.IntegerProperty()

        friends = models.Relationship('self',rel_type='friends_with')

Properties
==========

As you can see, some basic properties are provided::

    class OnlinePerson(Person):
        email = models.EmailProperty()
        homepage = models.URLProperty()

Some property types can also be indexed by neo4django. This will speed up
subsequent queries based on those properties::

    class EmployedPerson(Person):
        job_title = models.StringProperty(indexed=True)

All instances of ``EmployedPerson`` will have their ``job_title`` properties indexed.

For a list of included property types, check out :mod:`neo4django.db.models.__init__`.

Relationships
=============

Relationships are simple. Instead of :class:`~django.db.models.ForeignKey`,
:class:`~django.db.models.ManyToManyField`, or :class:`~django.db.models.OneToOneField`,
just use :class:`~neo4django.db.models.Relationship`. In addition to the 
relationship target, you can specify a relationship type and direction,
cardinality, and the name of the relationship on the target model::

    class Pet(models.NodeModel):
        owner = models.Relationship(Person, 
                                    rel_type='owns',
                                    single=True,
                                    related_name='pets'
                                   )

Note that specifying cardinality with ``single`` or ``rel_single`` is optional-
Neo4j doesn't enforce any relational cardinality. Instead, the options are
provided as a modeling convenience.

You can also target a model that has yet to be defined with a string::

    class Pet(models.NodeModel):
        owner = models.Relationship('Person', 
                                    rel_type='owns',
                                    single=True,
                                    related_name='pets'
                                   )

And then in the interpreter::

    >>> pete = Person.objects.create(name='Pete', age=30)
    >>> garfield = Pet.objects.create()
    >>> pete.pets.add(garfield)
    >>> pete.save()
    >>> list(pete.pets.all())
    [<Pet: Pet object>]

If you care about the order of a relationship, add the 
``preserve_ordering=True`` option. Related objects will be retrieved in the
order they were saved.

Got a few models written? To learn about retrieving data, see :doc:`querying`.

