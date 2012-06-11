#!/bin/bash

DEFAULT_VERSION="1.6"
VERSION=${1-$DEFAULT_VERSION}
DIR="neo4j-community-$VERSION"
FILE="$DIR-unix.tar.gz"
SERVER_PROPERTIES_FILE="lib/neo4j/conf/neo4j-server.properties"

if [[ ! -d lib/$DIR ]]; then
    wget http://dist.neo4j.org/$FILE
    tar xvfz $FILE &> /dev/null
    rm $FILE
    [[ ! -d lib ]] && mkdir lib
    mv $DIR lib/
    [[ -h lib/neo4j ]] && unlink lib/neo4j
    ln -fs $DIR lib/neo4j
    mkdir lib/neo4j/testing/
    DELETE_DB_PLUGIN_JAR="test-delete-db-extension-1.6.jar"
    wget --no-check-certificate -O lib/neo4j/testing/$DELETE_DB_PLUGIN_JAR https://github.com/downloads/jexp/neo4j-clean-remote-db-addon/$DELETE_DB_PLUGIN_JAR
    ln -s ../testing/$DELETE_DB_PLUGIN_JAR lib/neo4j/plugins/$DELETE_DB_PLUGIN_JAR
    cat >> $SERVER_PROPERTIES_FILE <<EOF
org.neo4j.server.thirdparty_jaxrs_classes=org.neo4j.server.extension.test.delete=/cleandb
org.neo4j.server.thirdparty.delete.key=supersecretdebugkey!
EOF
fi
