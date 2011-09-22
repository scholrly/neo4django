from setuptools import setup

setup(
    name='neo4django',
    version='0.1.4',
    author='Matt Luongo',
    author_email='mhluongo@gmail.com',
    description='A Django/Neo4j ORM layer based on the neo4j.py.',
    license = 'GPL',
    url = "http://packages.python.org/neo4django",
    packages=['neo4django','neo4django.db','neo4django.db.models','neo4django.tests'],
    long_description=open('README.rst').read(),
    platforms=['posix'],
    install_requires=[
        'decorator>=3.3.1',
        'jexp>=0.1.2',
        'neo4jrestclient>=1.4.5',
        'Django>=1.3',
    ],
    tests_require=[
        'nose>=1.0',
        'requests>=0.4.1',
        'nose-regression>=1.0',
    ],
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: POSIX',
    ],
    dependency_links = [
        'https://github.com/mhluongo/jexp/tarball/master#egg=jexp-0.1.2',
        'https://github.com/kennethreitz/requests/tarball/add6feab02d21967ca35f1572e6202b1dd11b788#egg=requests-0.4.1-py2.6-dev',
  ]
)
