class Neo4Django {
    static public binding;
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

    static getTypeNode(types) {
        def g = binding.g
        def locked = []
        def curVertex = g.v(0)
        def lockManager = getLockManager()
        def rawVertex, candidate, name, newTypeNode
        for (def typeProps : types) {
            rawVertex = curVertex.getRawVertex()
            lockManager.getWriteLock(rawVertex)
            locked << rawVertex

            candidate = curVertex.outE('<<TYPE>>').inV.find{
                it.map.subMap(typeProps.keySet()) == typeProps
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
        for (lockedRes in locked) {
            lockManager.releaseWriteLock(lockedRes, null)
        }

        curVertex
    }

    static createNodeWithTypes(types) {
        def g = binding.g
        def typeNode = getTypeNode(types)
        def newVertex = g.addVertex()
        g.addEdge(typeNode, newVertex, '<<INSTANCE>>', [:])
        newVertex
    }

    static indexNodeAsTypes(node, typeNames) {
        
    }
}
Neo4Django.binding = binding;
