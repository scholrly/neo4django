"""
Tests for built-in signals (e.g. 'post_delete', 'pre_save'), some of which
are sent by methods that neo4django overrides.

"""
from nose.tools import with_setup

from django.db.models import signals

def setup():
    global Person, gdb

    from neo4django.tests import Person, gdb

def teardown():
    gdb.cleandb()

def test_pre_init():
    pete = None
    result = {'sent': False}

    def handler(*args, **kwargs):
        result['sent'] = True
        if pete:
            assert False, "'pre_init' signal sent at the wrong time"

    signals.pre_init.connect(handler, sender=Person)

    pete = Person(name='Pete')

    if not result['sent']:
        assert False, "'pre_init' signal was not sent to handler"

    signals.pre_init.disconnect(handler, sender=Person)

def test_post_init():
    pete = None
    result = {'sent': False}

    def handler(instance=None, *args, **kwargs):
        result['sent'] = True
        if not (instance and isinstance(instance, Person)
                and instance.name == 'Pete'):
            assert False, "'post_init' signal sent at the wrong time"

    signals.post_init.connect(handler, sender=Person)

    pete = Person(name='Pete')

    if not result['sent']:
        assert False, "'post_init' signal was not sent to handler"

    signals.post_init.disconnect(handler, sender=Person)

@with_setup(None, teardown)
def test_pre_save():
    result = {'sent': False}

    def handler(instance=None, *args, **kwargs):
        result['sent'] = True
        # should be initialized but not yet committed to db
        if not (instance.name == 'Pete' and not instance.pk):
            assert False, "'pre_save' signal sent at the wrong time"

    signals.pre_save.connect(handler, sender=Person)

    pete = Person.objects.create(name='Pete')

    if not result['sent']:
        assert False, "'pre_save' signal was not sent to handler"

    signals.pre_save.disconnect(handler, sender=Person)

@with_setup(None, teardown)
def test_post_save():
    result = {'sent': False}

    def handler(instance=None, *args, **kwargs):
        result['sent'] = True
        try:
            # should have already been committed to db by now
            if not (instance.pk and Person.objects.get(pk=instance.pk)):
                assert False, "'post_save' signal sent at the wrong time"
        except Person.DoesNotExist:
            pass

    signals.post_save.connect(handler, sender=Person)

    pete = Person.objects.create(name='Pete')

    if not result['sent']:
        assert False, "'post_save' signal was not sent to handler"

    signals.post_save.disconnect(handler, sender=Person)

@with_setup(None, teardown)
def test_pre_delete():
    result = {'sent': False}

    def handler(instance=None, *args, **kwargs):
        result['sent'] = True
        try:
            # should still be in db
            if not (instance.pk and Person.objects.get(pk=instance.pk)):
                assert False, "'pre_delete' signal sent at the wrong time"
        except Person.DoesNotExist:
            pass

    signals.pre_delete.connect(handler, sender=Person)

    pete = Person.objects.create(name='Pete')
    pete.delete()

    if not result['sent']:
        assert False, "'pre_delete' signal was not sent to handler"

    signals.pre_delete.disconnect(handler, sender=Person)

@with_setup(None, teardown)
def test_post_delete():
    result = {'sent': False}

    def handler(instance=None, *args, **kwargs):
        result['sent'] = True
        try:
            Person.objects.get(pk=instance.pk)
            # we got here so person still exists
            assert False, "'post_delete' signal sent at the wrong time"
        except Person.DoesNotExist:
            # this is correct; should not be in db
            pass

    signals.post_delete.connect(handler, sender=Person)

    pete = Person.objects.create(name='Pete')
    pete.delete()

    if not result['sent']:
        assert False, "'post_delete' signal was not sent to handler"

    signals.post_delete.disconnect(handler, sender=Person)

