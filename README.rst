A neo4j/Django integration layer, based on `thobe`_'s original integration_ and `versae`_'s neo4jrestclient_.

.. _thobe: https://github.com/thobe/
.. _integration: http://journal.thobe.org/2009/12/seamless-neo4j-integration-in-django.html
.. _versae: https://github.com/versae/
.. _neo4jrestclient: https://github.com/versae/neo4j-rest-client/

Overview , or, What Does It Do?
===============================

neo4django is a Django ORM integration for neo4j. It allows model definitions that are very similar to Django's, and enables some other Django functionality (like signals).

The original Neo4j Django integration restricted database access to in-process. neo4django uses the Neo4j REST api to communicate with the database, freeing the database up for access by other processes and making it easy to host the database on another machine.

Other improvements over the original integration include

- A number of custom properties
    * ``EmailProperty``
    * ``IntegerProperty``
    * ``DateTimeProperty``
    * ``URLProperty``
- Improved indexing support.
- Index-based querying.
- Fancier QuerySet usage.
- A significant test suite to empower future development.
- Developed to Django 1.3.
- Built to work alongside relational models.

What It Doesn't Do (TODO)
=========================

`thobe`_'s list of future features hasn't been impacted that much by our development. We're still working on

- Relationship models and querying
- Neo4j-specific Manager API (to enable traversal, etc).
- And, to a lesser extent, support for the Django admin interface.

Getting Started
===================

Using pip, you can install from PyPi::

    pip install neo4django

or straight from GitHub::

    pip install -e https://github.com/scholrly/neo4django/

Database Setup
==============

An example settings.py::

    NEO4J_DATABASES = {
        'default' : {
            'HOST':'localhost',
            'PORT':7474,
            'ENDPOINT':'/db/data'
        }
    }

Note that if you want to use Django auth or other packages built on the regular relational ORM, you'll still need a regular ``DATABASES`` setting and a supported database.

Models
==========

These look just like the Django models you're used to, but instead of inheriting from ``django.db.model``, inherit from ``neo4django.NodeModel``::

    class Person(neo4django.NodeModel):
        name = neo4django.StringProperty()
        age = neo4django.IntegerProperty()

Properties
==========

As you can see, some basic properties are provided::

    class OnlinePerson(Person):
        email = neo4django.EmailProperty()
        homepage = neo4django.URLProperty()

Some property types can also be indexed by neo4django. This will speed up subsequent queries based on those properties::

    class EmployedPerson(Person):
        job_title = neo4django.StringProperty(indexed=True)

All instances of ``EmployedPerson`` will have their ``job_title`` properties indexed.

This might be a good time to mention a couple caveats.
1. Properties of value ``None`` are not currently indexed. I know, I'm sorry - working on it.
2. neo4django doesn't come with a migration tool! (Though if you're interested in writing one, talk to us!) If you flip a property to ``indexed=True``, make sure you update the graph manually, or re-index your models by resetting the property (per affected model instance) and saving.

Relationships
=============

Relationships are supported as in the original integration::

    class Pet(neo4django.NodeModel):
        owner = neo4django.Relationship(Person, 
                                        rel_type=neo4django.Incoming.OWNS,
                                        single=True,
                                        related_name='pets'
                                       )

And then in the interpreter::

    >>> pete = Person.objects.create(name='Pete', age=30)
    >>> garfield = Pet.objects.create()
    >>> pete.pets.add(garfield)
    >>> pete.save()
    >>> list(pete.pets.all())
    [<Pet: Pet object]

You can also add a new option, ``preserve_ordering``, to the ``Relationship``. In that case, the order of relationship creation will be persisted.

Relationships caveat - currently, lazy initialization (``neo4django.Relationship("Person",...``) is borked, but should be fixed soon.

QuerySets
=========

QuerySets now implement more of the `Django QuerySet API`_, like ``get_or_create``.

They accept a slew of useful field lookups- namely

- exact
- gt
- lt
- gte
- lte
- and range
More will be implemented soon - they're pretty easy, and a great place to contribute!

QuerySets take advantage of indexed properties, typing, and REST paged traversals to get you what you want, faster.

.. _Django QuerySet API: https://docs.djangoproject.com/en/1.3/ref/models/querysets/

Working Alongside Django ORM
============================

If you'd like to use Django with Neo4j and a relational database, we've got you covered. Simply install the provided database router, and the two ORMs will play nice.

In you settings.py::

    DATABASE_ROUTERS = ['neo4django.utils.Neo4djangoIntegrationRouter']

Performance
===========

We have a *long* way to go in the performance department. neo4django isn't currently taking advantage of a number of performance improvements that have recently become available in the REST client. There are a number of hotspots that could be improved by using the new batch/transactional support, and more gains could be made by abusing Javascript parameters in the REST API.

That said, we don't have benchmarks showing poor performance, either ;)

Multiple Databases
==================

We wrote neo4django to support multiple databases- but haven't tested it. In the future, we'd like to fully support multiple databases and routing similar to that already in Django.

Further Introspection
=====================

When possible, neo4django follows Django ORM, and thus allows some introspection of the schema. Because Neo4j is schema-less, though, further introspection and a more dynamic data layer can be handy. Initially, there's only one additional option to enable decoration of ``Property`` s and ``Relationship`` s - ``metadata`` ::

    class N(NodeModel):
        name = StringProperty(metadata={'authoritative':True})
        aliases = StringArrayProperty(metadata={'authoritative':False, 'authority':name})

    >>> for field in N._meta.fields:
    ...     if hasattr(field, 'meta'):
    ...         if field.meta['authoritative']:
    ...             #do something with the authoritative field

Running the Test Suite
======================

The test suite requires that Neo4j be running on localhost:7474, and that you have the cleandb_ extension installed.

We test with nose_. To run the suite, set ``test_settings.py`` as your ``DJANGO_SETTINGS_MODULE`` and run ``nosetests``. In bash, that's simply::

    cd <your path/neo4django/
    export DJANGO_SETTINGS_MODULE="neo4django.tests.test_settings"
    nosetests

We've put together a nose plugin_ to ensure that regression tests pass. Any changesets that fail regression tests will be denied a pull. To run the tests, simply::

    pip install nose-regression
    nosetests --with-regression

.. _cleandb: https://github.com/jexp/neo4j-clean-remote-db-addon
.. _nose: http://readthedocs.org/docs/nose/en/latest/
.. _plugin: https://github.com/scholrly/nose-regression

Contributing
============

All contributions, no matter how small, are greatly appreciated!

If you do decide to contribute, please test! If a pull request fails any regression tests, we won't be able to accept it.

