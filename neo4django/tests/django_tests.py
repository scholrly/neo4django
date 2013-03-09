from nose.tools import with_setup, eq_

from django.conf import settings
TEST_SQL_DB_NAME = settings.DATABASES.get('default',{}).get('NAME','')

import os

def setup():
    global Person, gdb, models

    from neo4django.tests import Person, gdb
    from neo4django.db import models

def teardown():
    gdb.cleandb()

def test_json_serialize():
    from django.core import serializers
    dave = Person(name='dave')
    dave.save()
    json_serializer = serializers.get_serializer('json')()
    assert json_serializer.serialize(Person.objects.all())
    dave = Person(name='dave', age=12)
    dave.save()
    assert json_serializer.serialize(Person.objects.all())
    dave = Person()
    dave.save()
    assert json_serializer.serialize(Person.objects.all())

def touch_test_db():
    db_dir = os.path.dirname(TEST_SQL_DB_NAME)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    open(TEST_SQL_DB_NAME,'w').close()

def rm_test_db():
    os.remove(TEST_SQL_DB_NAME)

@with_setup(touch_test_db, rm_test_db)
def test_syncdb():
    from django.core.management import call_command
    call_command('syncdb', interactive=False)

def test_auth():
    from neo4django.auth.models import User
    user = User.objects.create_user('john', 'lennon@thebeatles.com', 'johnpassword')

    from django.contrib.auth import authenticate
    eq_(authenticate(username='john', password='johnpassword'), user)

def test_modelform():
    from django.forms import ModelForm

    class PersonForm(ModelForm):
        class Meta:
            model = Person

    person_form = PersonForm()
    as_p = person_form.as_p()
    assert 'id_age' in as_p
    assert 'id_name' in as_p
