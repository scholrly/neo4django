==========
Migrations
==========

neo4django doesn't come with a migration tool. If you flip a property to
``indexed=True`` or change a relationship, make sure you update the graph
manually to reflect the change. In the case of newly index properties,
re-index your models by resetting the property (per affected model instance)
and saving.
