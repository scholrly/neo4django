from nose.tools import eq_

def setup():
    global Person, neo4django, settings, gdb, models

    from neo4django.tests import Person, neo4django, gdb, models

def teardown():
    gdb.cleandb()

def test_basic_relationship():
    """
    Tests both sides of a simple many-to-many relationship (without relationship
    properties).
    """
    class RelatedPaper(models.NodeModel):
        authors = models.Relationship(Person,
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

def test_basic_relationship_manager():
    class SomeOtherPaper(models.NodeModel):
        authors = models.Relationship(Person,
                rel_type = neo4django.Outgoing.OWNED_BY,
                related_name = 'papers'
            )
    #from nose.tools import set_trace; set_trace()
    pete = Person.objects.create(name="PETE!")
    boring_paper = SomeOtherPaper()
    boring_paper.authors.add(pete)
    eq_(list(boring_paper.authors.all()), [pete])
    
    boring_paper.authors.remove(pete)
    eq_(list(boring_paper.authors.all()), [])
    
    other_paper = SomeOtherPaper.objects.create()
    other_paper.authors.add(pete)
    other_paper.authors.clear()
    eq_(list(other_paper.authors.all()), [])

def test_one_to_many():
    class Origin1(models.NodeModel):
        name = models.StringProperty()

    class Reference1(models.NodeModel):
        origin = models.Relationship(Origin1,
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
    class Origin2(models.NodeModel):
        name = models.StringProperty()

    class Reference2(models.NodeModel):
        origin = models.Relationship(Origin2,
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
    class AnotherReference(models.NodeModel):
        pass

    class AnotherOrigin(models.NodeModel):
        name = models.StringProperty()
        references = models.Relationship(AnotherReference,
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
    class AnotherReference1(models.NodeModel):
        pass

    class AnotherOrigin1(models.NodeModel):
        name = models.StringProperty()
        references = models.Relationship(AnotherReference1,
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
    class Stalker(models.NodeModel):
        name = models.StringProperty()
        person = models.Relationship(Person,
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
    class Actor(models.NodeModel):
        name = models.StringProperty()
        def __str__(self):
            return self.name

    class MovieCredits(models.NodeModel):
        actors = models.Relationship(Actor,
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
    class Authorship(models.Relationship):
        when = models.DateProperty()
    class ComplexRelatedPaper(models.NodeModel):
        pass
    raise NotImplementedError("Write this test!")

def test_multinode_setting():
    """Tests setting a multi-node relationship directly instead of adding."""
    class Classroom(models.NodeModel):
        students = models.Relationship(Person,
                                rel_type=neo4django.Outgoing.COMES_TO,
                                related_name="school"
                                )
    class Student(models.NodeModel):
        name = models.StringProperty()
        def __str__(self):
            return self.name

    students = [Student(name=name) for name in ['Violet', 'Grigori', 'Kaden', 'Gluz']]
    classroom = Classroom()
    classroom.students = students[:2]
    assert len(list(classroom.students.all())) == 2
    classroom.students.add(students[2])
    assert len(list(classroom.students.all())) == 3
    classroom.students = students[3:]
    classroom.save()
    assert len(list(classroom.students.all())) == 1

def test_rel_metadata():
    class NodeWithRelMetadata(models.NodeModel):
        contacts = models.Relationship(Person,
                                           rel_type=neo4django.Outgoing.KNOWS,
                                           metadata={'test':123})
    meta_fields = filter(lambda f: hasattr(f, 'meta'), NodeWithRelMetadata._meta.fields)
    eq_(len(meta_fields), 1)
    assert 'test' in meta_fields[0].meta
    eq_(meta_fields[0].meta['test'], 123)

def test_rel_self():
    class MetaNode(models.NodeModel):
        myself = models.Relationship('self', 'IS', single=True, related_name = 'myselves')

    meta = MetaNode()
    meta.myself = meta
    meta.save()

    eq_(meta.myself, meta)
    assert meta in meta.myselves.all()

def test_rel_string_target():
    class Child(models.NodeModel):
        parents = models.Relationship('neo4django.Person', 'CHILD_OF')

    child = Child()
    child.parents.add(Person.objects.create(name='Han'))
    child.parents.add(Person.objects.create(name='Leia'))
    child.save()

    eq_(('Han','Leia'), tuple(sorted(p.name for p in child.parents.all())))
