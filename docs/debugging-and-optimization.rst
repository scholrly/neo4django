========================
Debugging & Optimization
========================

A django-debug-toolbar_ panel_ has been written to make debugging Neo4j REST
calls easier. It should also help debugging and optimizing neo4django.


:func:`neo4django.testcases.NodeModelTestCase.assertNumRequests` can also help
by ensuring round trips in a piece of test code don't grow unexpectedly.

.. _django-debug-toolbar: https://github.com/django-debug-toolbar/django-debug-toolbar
.. _panel: https://github.com/robinedwards/django-debug-toolbar-neo4j-panel/
