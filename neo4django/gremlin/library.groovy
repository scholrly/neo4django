import org.neo4j.helpers.collection.MapUtil
import org.neo4j.graphdb.index.IndexManager
import com.tinkerpop.blueprints.pgm.impls.neo4j.Neo4jIndex

import org.neo4j.cypher.javacompat.ExecutionEngine

class Neo4Django {
    static public binding
    static transactions = []
    static bufferSizes = []
    static parsedCypher = [:]
    static final AUTO_PROP_INDEX_KEY = 'LAST_AUTO_VALUE'
    static final UNIQUENESS_ERROR_MESSAGE = 'neo4django: uniqueness error'
    static final INTERNAL_ATTR='_neo4django'
    static final TYPE_ATTR=INTERNAL_ATTR + '_type'
    static final ERROR_ATTR=INTERNAL_ATTR + '_error'

    static cypher(queryString, params) {
        def query, engine = new ExecutionEngine(binding.g.getRawGraph())
        if (parsedCypher.containsKey(queryString)) {
            query = parsedCypher[queryString]
        } else {
            try {
                def parser = this.class.classLoader.loadClass(
                        "org.neo4j.cypher.javacompat.CypherParser")\
                        .newInstance()
                query = parser.parse(queryString)
                parsedCypher[queryString] = query
            }
            catch (Exception e){
                query = queryString
            }
        }
        return engine.execute(query, params)
    }

    static getModelTypes(nodes){
        /* Return a table with a node and its neo4django type name.*/
        //TODO
        //results = cypher('')
        //rv = Table()
    }

    static getNeo4djangoErrorMap(message, map) {
        def errorMap = ["${ERROR_ATTR}":true]
        errorMap.putAll(map)
        errorMap['message'] = message
        return errorMap
    }

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

    //TODO this might suffer from a problem similar to #54
    static indexNodeAsTypes(node, indexName, typeNames) {
        def (index, rawIndex) = getOrCreateIndex(indexName)
        def rawVertex = node.getRawVertex()
        for(name in typeNames) {
            rawIndex.add(rawVertex, TYPE_ATTR, name)
        }   
    }

    static updateNodeProperties(node, propMap) {
        def originalVal, closureString, value, lastAutoProp, autoDefault, index
        def oldNodeIds, valuesToIndex, rawIndex, indexName, error = null, typeNode
        def types, g = binding.g
        propMap.each{prop, dict ->
            if (dict.get('auto_increment')){
                if (dict.get('auto_abstract')) {
                    types = [['app_label':dict['auto_app_label'],
                              'model_name':dict['auto_model']]]
                    typeNode = getTypeNode(types)
                }
                else {
                    typeNode = getTypeNodeFromNode(node)
                }
                //get a ghetto write lock on the type node
                getGhettoWriteLock(typeNode)

                lastAutoProp = prop + '.' + AUTO_PROP_INDEX_KEY
                closureString = dict.get('increment_func') ?: '{i -> i+1}'
                
                if (typeNode.map().containsKey(lastAutoProp)){
                    value = singleArgEval(closureString, typeNode[lastAutoProp])
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
            if (dict.containsKey('index_name')) {
                indexName = dict['index_name']
                (index, rawIndex) = getOrCreateIndex(indexName)
                valuesToIndex = dict.get('values_to_index') ?: []
                if (valuesToIndex.size() == 0 && dict.containsKey('to_index_func')) {
                    valuesToIndex << singleArgEval(dict.get('to_index_func') ?: '{i->i}', value)
                }
                if (dict.get('unique')){
                    //TODO take care of unique vs array membership indexing
                    //TODO eventually the prop name and index key should be decoupled
                    oldNodeIds = index.get(prop, valuesToIndex[0])*.id
                    if (oldNodeIds.size() > 0 && !oldNodeIds.contains(node.id)){
                        error = getNeo4djangoErrorMap(UNIQUENESS_ERROR_MESSAGE, [property:prop, 'old':oldNodeIds])
                        return error
                    }
                }
                //totally remove the node for a key
                rawIndex.remove(node.getRawVertex(), prop)
                //and reindex it
                for(v in valuesToIndex) {
                    if (v != null ){
                        rawIndex.add(node.getRawVertex(), prop, v)
                    }
                }
            }

            //set the value
            if (value == null){
                node.removeProperty(prop)
            }
            else {
                if (value instanceof List) {
                    if (value[0] instanceof Integer || value[0] instanceof Long) {
                        value = value as Long[]
                    }
                    else {
                        value = value as String[]
                    }
                }
                node[prop] = value
            }
        }
        if (error != null){
            return error
        }
        return node
    }

    static singleArgEval(closureString, original) {
        Eval.x(original, closureString + "(x)")
    }

    static getVerticesByIds(ids) {
        // GREMLIN HACK ALERT: This is a workaround because g.v() can't
        //                     take more than 250 elements by itself.
        //                     According to gremlin devs, this is equiv
        def res = [], v
        ids.each{
            if(it != null){
                v = binding.g.v(it)
                if (v != null){ res << v }
            }
        }
        return res
    }
}
Neo4Django.binding = binding;
