import org.neo4j.helpers.collection.MapUtil
import org.neo4j.graphdb.index.IndexManager
import com.tinkerpop.blueprints.pgm.impls.neo4j.Neo4jIndex;

class Neo4Django {
    static public binding;
    static transactions = [];
    static bufferSizes = [];
    static final AUTO_PROP_INDEX_KEY = 'LAST_AUTO_VALUE';
    static final UNIQUENESS_ERROR_MESSAGE = 'neo4django: uniqueness error';
    static queryNodeIndices(queries) {
        /**
        * Returns the intersection of multiple node index queries.
        *
        * @param queries a list of (query name, query string) pairs.
        */
        def neo4j = binding.g.getRawGraph()

        //pull all nodes from indexes
        //TODO run the javascript expression on each node, and only return if true
        //TODO check all the types and make sure they match, or don't return
        def nodes = [] as Set, index, rawIndex
        for (def q : queries) {
            (index, rawIndex) = getOrCreateIndex(q[0])
            if (rawIndex != null) {
                def newNodes = rawIndex.query(q[1])
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

    static startTx() {
        bufferSizes << binding.g.getMaxBufferSize()
        binding.g.setMaxBufferSize(0)
        def tx = binding.g.getRawGraph().beginTx()
        transactions << tx
        return tx
    }

    static finishTx(success) {
        def oldBufferSize = bufferSizes.pop()
        def tx = transactions.pop()
        if (success) tx.success(); else tx.failure()
        tx.finish()
        binding.g.setMaxBufferSize(oldBufferSize)
    }

    static passTx() {
        finishTx(true)
    }

    static failTx() {
        finishTx(false)
    }

    static getTypeNode(types) {
        def g = binding.g
        def originalBufferSize = g.getMaxBufferSize()
        startTx()
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
            passTx()
            return curVertex
        }
        catch (Exception e){
            failTx()
            throw e
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

    //returns 
    static getOrCreateIndex(indexName) {
        def g = binding.g
        def index = g.idx(indexName), rawIndex = null
        if (!index){
            def opts = MapUtil.stringMap(IndexManager.PROVIDER, "lucene", "type", "fulltext")
            rawIndex = g.getRawGraph().index().forNodes(indexName, opts)
            //XXX can't use g.idx because gremlin doesn't get indices dynamically
            index = new Neo4jIndex(indexName, Vertex.class, g)
        }
        else {
            rawIndex = g.getRawGraph().index().forNodes(indexName)
        }
        return [index, rawIndex]
    }

    static getRawIndex(indexName) {
        def indexManager = binding.g.getRawGraph().index()
        return indexManager.existsForNodes(indexName)? indexManager.forNodes(indexName) : null
    }

    static indexNodeAsTypes(node, typeNames) {
        //TODO!
    }

    static updateNodeProperties(node, propMap, indexName) {
        def originalVal, closureString, value, lastAutoProp, autoDefault, index
        def oldNodes, valuesToIndex, rawIndex, error = null, g = binding.g
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
            if (dict.containsKey('values_to_index')) {
                (index, rawIndex) = getOrCreateIndex(indexName)
                valuesToIndex = dict.get('values_to_index') ?: []
                if (dict.get('unique')){
                    //TODO take care of unique vs array membership indexing
                    //eventually the prop name and index key should be decoupled
                    oldNodes = index.get(prop, valuesToIndex[0])
                    if (oldNodes.size() > 0 && !oldNodes*.id.contains(node.id)){
                        error = UNIQUENESS_ERROR_MESSAGE
                        return error
                    }
                }
                //totally remove the node for a key
                rawIndex.remove(node.getRawVertex(), prop)
                //and reindex it
                for(v in valuesToIndex) {
                    if (v != null ){
                        index.put(prop, v, node)
                    }
                }
            }

            //set the value
            if (value == null){
                node.removeProperty(prop)
            }
            else {
                if (value instanceof List) {
                    value = value as String[]
                }
                node[prop] = value
            }
        }
        if (error != null){
            return error
        }
        return node
    }

    static getNextAutoValue(original, closureString) {
        Eval.x(original, closureString + "(x)")
    }
}
Neo4Django.binding = binding;
