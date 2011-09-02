from nose.tools import eq_

def setup():
    global Person, neo4django, settings, gdb

    from neo4django.tests import Person, neo4django, gdb

def teardown():
    gdb.cleandb()

def test_basic_relationship():
    """
    Tests both sides of a simple many-to-many relationship (without relationship
    properties).
    """
    class RelatedPaper(neo4django.NodeModel):
        authors = neo4django.Relationship(Person,
                rel_type = neo4django.Outgoing.OWNED_BY,
                related_name = 'papers'
            )
    
    sandra = Person(name="Sandra")
    sandra.save()
    lifesWork = RelatedPaper()
    lifesWork.save()
    lifesWork.authors.add(sandra)
    lifesWork.save()
    work = list(sandra.papers.all())
    assert lifesWork in work, "Paper not found in %s" % repr(work)
    authors = list(lifesWork.authors.all())
    assert sandra in authors, "Author not found in %s" % repr(work)

def test_one_to_many():
    class Origin1(neo4django.NodeModel):
        name = neo4django.StringProperty()

    class Reference1(neo4django.NodeModel):
        origin = neo4django.Relationship(Origin1,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='references',
                                         single=True)

    origin = Origin1(name='CNN')
    origin.save()
    ref = Reference1()
    ref.origin = origin
    ref.save()
    assert ref.origin.name == origin.name, "The single side doesn't work!"
    assert len(list(origin.references.all())) == 1, \
            "Adding to the single side doesn't update the many side."

def test_many_to_one():
    class Origin2(neo4django.NodeModel):
        name = neo4django.StringProperty()

    class Reference2(neo4django.NodeModel):
        origin = neo4django.Relationship(Origin2,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         #TODO explore edge direction here, this is wrong
                                         related_name='references',
                                         single=True)
    origin = Origin2(name='CNN')
    origin.save()
    ref = Reference2()
    ref.save()
    origin.references.add(ref)
    origin.save()
    assert ref.origin and (ref.origin.name == origin.name), \
           "Adding to the many side doesn't update the single side."
    assert len(list(origin.references.all())) == 1, "The many side doesn't work!"

def test_related_one_to_many():
    class AnotherReference(neo4django.NodeModel):
        pass

    class AnotherOrigin(neo4django.NodeModel):
        name = neo4django.StringProperty()
        references = neo4django.Relationship(AnotherReference,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='origin',
                                         related_single=True)

    origin = AnotherOrigin(name='CNN')
    origin.save()
    ref = AnotherReference()
    ref.origin = origin
    ref.save()
    assert ref.origin.name == origin.name, "The single side doesn't work!"
    assert len(list(origin.references.all())) == 1, \
            "Adding to the single side doesn't update the many side."

def test_related_many_to_one():
    class AnotherReference1(neo4django.NodeModel):
        pass

    class AnotherOrigin1(neo4django.NodeModel):
        name = neo4django.StringProperty()
        references = neo4django.Relationship(AnotherReference1,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='origin',
                                         related_single=True)
    origin = AnotherOrigin1(name='CNN')
    ref = AnotherReference1()
    ref.save()
    ref2 = AnotherReference1()
    ref2.save()
    origin.references.add(ref)
    origin.references.add(ref2)
    origin.save()
    assert ref.origin and (ref.origin.name == origin.name), \
           "Adding to the many side doesn't update the single side."
    assert len(list(origin.references.all())) == 2, "The many side doesn't work!"

def test_one_to_one():
    class Stalker(neo4django.NodeModel):
        name = neo4django.StringProperty()
        person = neo4django.Relationship(Person,
                                            rel_type=neo4django.Outgoing.POINTS_TO,
                                            single=True,
                                            related_single=True
                                        )
    p = Person.objects.create(name='Stalked')
    s = Stalker(name='Creeper')
    s.person = p
    s.save()

    #test that the one-to-one is correct after a retrieval
    new_s = list(Stalker.objects.all())[0]
    eq_(new_s.person, p)

def test_ordering():
    class Actor(neo4django.NodeModel):
        name = neo4django.StringProperty()
        def __str__(self):
            return self.name

    class MovieCredits(neo4django.NodeModel):
        actors = neo4django.Relationship(Actor,
                                         rel_type=neo4django.Incoming.ACTS_IN,
                                         related_name='movies',
                                         preserve_ordering=True,
                                        )

    actors = [Actor(name=n) for n in ['Johnny','Angelina','Jennifer','Tobey']]
    for a in actors: a.save()
    
    superhero_flick = MovieCredits()
    superhero_flick.save()
    for a in actors: superhero_flick.actors.add(a)
    superhero_flick.save()

    node = superhero_flick.node
    del superhero_flick
    same_flick = MovieCredits._neo4j_instance(node)
    assert actors == list(same_flick.actors.all())

    same_flick.actors.remove(actors[1])
    same_flick.save()
    del same_flick

    same_flick = MovieCredits._neo4j_instance(node)
    flick_actors = list(same_flick.actors.all())
    should_be = [actors[0]] + actors[2:]
    assert should_be == flick_actors, "%s should be %s" % (str(flick_actors), str(should_be))

def test_relationship_model():
    """Tests both sides of a many-to-many relationship with attached properties & model."""
    class Authorship(neo4django.Relationship):
        when = neo4django.DateProperty()
    class ComplexRelatedPaper(neo4django.NodeModel):
        pass

def test_multinode_setting():
    """Tests setting a mutli-node relationship directly instead of adding."""
    class Classroom(neo4django.NodeModel):
        students = neo4django.Relationship(Person,
                                rel_type=neo4django.Outgoing.COMES_TO,
                                related_name="school"
                                )
    class Student(neo4django.NodeModel):
        name = neo4django.StringProperty()
        def __str__(self):
            return self.name

    students = [Student(name=name) for name in ['Violet', 'Grigori', 'Kaden', 'Gluz']]
    classroom = Classroom()
    classroom.students = students[:2]
    assert len(list(classroom.students.all())) == 2
    classroom.students.add(students[2])
    assert len(list(classroom.students.all())) == 3
    classroom.students = students[3:]
    assert len(list(classroom.students.all())) == 1
