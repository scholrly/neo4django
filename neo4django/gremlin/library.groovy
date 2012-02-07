class Neo4Django {
    static public binding;
    static final AUTO_PROP_INDEX_KEY = 'LAST_AUTO_VALUE';
    static queryNodeIndices(queries) {
        /**
        * Returns the intersection of multiple node index queries.
        *
        * @param queries a list of (query name, query string) pairs.
        */
        def neo4j = binding.g.getRawGraph()
        def indexManager = neo4j.index()

        //pull all nodes from indexes
        //TODO run the javascript expression on each node, and only return if true
        //TODO check all the types and make sure they match, or don't return
        def nodes = [] as Set
        def index
        for (def q : queries) {
            index = indexManager.forNodes(q[0])
            if (index != null) {
                def newNodes = index.query(q[1])
                if (newNodes != null) {
                    if (nodes.size() == 0) {
                        for (def n: newNodes) {
                            nodes.add(n)
                        }
                    }
                    else {
                        nodes = nodes.intersect(newNodes)
                    }
                }
                if(nodes.size() == 0) {
                    break
                }
            }
        }
        nodes
    }
    
    static getLockManager() {
        binding.g.getRawGraph().getConfig().getLockManager()
    }

    static getGhettoWriteLock(element){
        element.removeProperty('thisPropshouldxneverbeused')
    }

    static getTypeNode(types) {
        //there still might be a problem here...
        def g = binding.g
        def originalBufferSize = g.getMaxBufferSize()
        g.setMaxBufferSize(0); g.startTransaction()
        try {
            def curVertex = g.v(0)
            def candidate, name, newTypeNode
            for (def typeProps : types) {
                getGhettoWriteLock(curVertex)
                candidate = curVertex.outE('<<TYPE>>').inV.find{
                    it.map().subMap(typeProps.keySet()) == typeProps
                }
                if (candidate == null) {
                    newTypeNode = g.addVertex(typeProps)
                    name = typeProps['app_label'] + ":" + typeProps['model_name']
                    newTypeNode.name = name
                    g.addEdge(curVertex, newTypeNode, "<<TYPE>>")
                    curVertex = newTypeNode
                }
                else {
                    curVertex = candidate
                }
            }
            g.stopTransaction(TransactionalGraph.Conclusion.SUCCESS);
            return curVertex
        }
        catch (Exception e){
            g.stopTransaction(TransactionalGraph.Conclusion.FAILURE);
            throw e
        }
        finally {
            g.setMaxBufferSize(originalBufferSize)
        }
    }

    static getTypeNodeFromNode(node) {
        node.in('<<INSTANCE>>').next()
    }

    static createNodeWithTypes(types) {
        def g = binding.g
        def typeNode = getTypeNode(types)
        def newVertex = g.addVertex()
        g.addEdge(typeNode, newVertex, '<<INSTANCE>>', [:])
        newVertex
    }

    static createIndex(indexName) {
    }

    static indexNodeAsTypes(node, typeNames) {
        
    }

    static updateNodeProperties(node, propMap, indexName) {
        def originalVal, closureString, value, lastAutoProp, autoDefault, index
        def oldNode, valuesToIndex, oldValue
        def typeNode = getTypeNodeFromNode(node)
        propMap.each{prop, dict ->
            if (dict.get('auto_increment')){
                //get a ghetto write lock on the type node
                getGhettoWriteLock(typeNode)

                lastAutoProp = prop + '.' + AUTO_PROP_INDEX_KEY
                closureString = dict.get('increment_func') ?: '{i -> i+1}'
                
                if (typeNode.map().containsKey(lastAutoProp)){
                    value = getNextAutoValue(typeNode[lastAutoProp], closureString)
                }
                else {
                    autoDefault = dict.get('auto_default')
                    value = (autoDefault != null) ? autoDefault : 1
                }
                typeNode[lastAutoProp] = value
            }
            else {
                value = dict.get('value')
            }
            oldValue = node[prop]
            //set the value
            if (value == null){
                node.removeProperty(prop)
            }
            else {
                node[prop] = value
            }
            if (dict.get('indexed')) {
                index = binding.g.idx(indexName)
                valuesToIndex = dict.get('values_to_index') ?: [null]
                if (dict.get('unique')){
                    //eventually the prop name and index key should be decoupled
                    oldNode = index[[prop:valuesToIndex[0]]]
                    if (oldNode != null && oldNode.id != node.id){
                        //TODO HOUSTON WE HAVE A COLLISION
                    }
                }
                //totally remove the node for a key
                index.getRawIndex().remove(node, prop)
                for(v in valuesToIndex) {
                    if (v != null ){
                        index.put(prop, v, node)
                    }
                }
            }
        }
    }

    static getNextAutoValue(original, closureString) {
        Eval.x(original, closureString + "(x)")
    }
}
Neo4Django.binding = binding;
