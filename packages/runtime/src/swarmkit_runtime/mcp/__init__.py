"""MCP integration (design §18).

  - Client: invoke MCP tools from capability skills
  - Server lifecycle: sandbox-supervised startup for scaffolded MCP servers
    (Docker / equivalent, per §8.8 and §21 sandboxing question)
  - AGT security gateway wraps every MCP call — policy enforcement before
    execution (§18.1)
"""
