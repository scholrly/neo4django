===================
The Admin Interface
===================

After a few settings tweaks, you can use the admin interface.

You'll need neo4django's :doc:`auth` working properly, as well as its
prerequisites like :mod:`django.contrib.sessions`.

Add :mod:`neo4django.admin` and :mod:`neo4django.contenttypes` to your
``INSTALLED_APPS``. Also include :mod:`django.contrib.admin` and
:mod:`django.contrib.contenttypes`, but make sure they come after the neo4django
versions.

In your `urls.py`, instead of importing :mod:`django.contrib.admin`, import 
:mod:`neo4django.admin`::

    from neo4django import admin
    
    admin.autodiscover()

    urlpatterns = patterns('',
        ...
        (r'^admin/', include(admin.site.urls)),
    )

And in your app's `admin.py`, do the same::

    from neo4django import admin
    from my_app.models import MyModel
    
    class MyModelAdmin(admin.ModelAdmin):
        ...
    
    admin.site.register(MyModel, MyModelAdmin)

Since we don't use `syncdb`, you probably won't have created a neo4django
superuser. Run `manage.py shell` and create a superuser with::

    from neo4django.graph_auth.models import User
    User.objects.create_superuser('matt', 'matt@emailprovider.com', 'password')
  
Run `manage.py runserver`, and visit http://localhost:8000/admin. Voila. Sign
in and enjoy.

Usage with Relational Databases
===============================

The integration hasn't been tested using both Neo4j and a relational database.
The two databases certainly wouldn't be able to share an admin site, but it
might be possible to run them as separate admin sites with their own URLs.

As example routing might look like::


    from django.contrib import admin
    admin.autodiscover()
    
    from neo4django import admin as neo_admin
    neo_admin.autodiscover()
     
    urlpatterns = patterns('',
        (r'^admin/', include(admin.site.urls)),
        (r'^neo_admin/', include(neo_admin.site.urls))
    )

If you give this a try, please let us know how it goes!

Limitations
===========

The integration is new, and only basic features have been tested. Known
limitations include broken "View on Site" and "History" buttons, but more will
surely be found. If you have any trouble, please `raise an issue`_!

.. _raise an issue: https://github.com/scholrly/neo4django/issues/
