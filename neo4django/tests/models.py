from neo4django.db import models

class IndexedMouse(models.NodeModel):
    name = models.StringProperty(indexed=True)
    age = models.IntegerProperty(indexed=True)

class RelatedCat(models.NodeModel):
    name = models.StringProperty()
    chases = models.Relationship(IndexedMouse, rel_type='chases')

class RelatedDog(models.NodeModel):
    name = models.StringProperty()
    chases = models.Relationship(RelatedCat, rel_type='chases')

class LazyCat(models.NodeModel):
    name = models.StringProperty()
    chases = models.Relationship('IndexedMouse', rel_type='chases_lazily')

