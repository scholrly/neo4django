from setuptools import setup

setup(
    name='neo4django',
    version='0.1.8',
    author='Matt Luongo',
    author_email='mhluongo@gmail.com',
    description='A Django/Neo4j ORM layer.',
    license = 'GPL',
    url = "https://neo4django.readthedocs.org/en/latest/",
    packages=['neo4django','neo4django.db','neo4django.db.models','neo4django.tests', 'neo4django.gremlin'],
    package_dir={'neo4django':'neo4django'},
    package_data={'neo4django':['gremlin/*.groovy']},
    long_description=open('README.rst').read(),
    platforms=['posix'],
    install_requires=[
        'decorator>=3.3.1',
        'python-dateutil>=2.0',
        'neo4jrestclient>=1.7',
        'Django>=1.3,<1.5',
        'lucene-querybuilder>=0.1.6'
    ],
    tests_require=[
        'nose>=1.0',
        'requests>=0.4.1',
        'nose-regression>=1.0',
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Framework :: Django',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: POSIX',
    ],
    dependency_links = [
        'https://github.com/mhluongo/jexp/tarball/master#egg=jexp-0.1.2',
        'https://github.com/kennethreitz/requests/tarball/add6feab02d21967ca35f1572e6202b1dd11b788#egg=requests-0.4.1-py2.6-dev',
  ]
)
