# SwarmKit demo recording for README
# Run with: vhs scripts/demo.tape
# Produces: docs/images/demo.gif
#
# Prerequisites:
#   - brew install charmbracelet/tap/vhs (or go install github.com/charmbracelet/vhs@latest)
#   - pip install swarmkit-runtime (or uv tool install swarmkit-runtime)
#   - ANTHROPIC_API_KEY or OPENROUTER_API_KEY set in environment
#   - Clean directory (no existing my-demo/ folder)

Output docs/images/demo.gif
Set Shell "bash"
Set FontSize 14
Set Width 1000
Set Height 600
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 40ms

# Title
Type "# SwarmKit: Multi-agent AI swarms as YAML, not code."
Enter
Sleep 1s
Type "clear"
Enter
Sleep 500ms

# Step 1: Install
Type "# Step 1: Install SwarmKit"
Enter
Sleep 800ms
Type "pip install swarmkit-runtime"
Enter
Sleep 3s

# Step 2: Validate the reference workspace
Type "# Step 2: Validate the built-in Code Review Swarm"
Enter
Sleep 800ms
Type "swarmkit validate reference/ --tree"
Enter
Sleep 3s

# Step 3: Dry run to see what would execute
Type "# Step 3: Dry run — see the agent tree without hitting any LLM"
Enter
Sleep 800ms
Type "swarmkit run reference/ code-review --dry-run"
Enter
Sleep 3s

# Step 4: Run a real topology
Type "# Step 4: Run the hello-swarm example"
Enter
Sleep 800ms
Type "swarmkit run examples/hello-swarm/workspace hello --input 'Greet the engineering team'"
Enter
Sleep 10s

# Step 5: Check status
Type "# Step 5: Check what happened"
Enter
Sleep 800ms
Type "swarmkit status examples/hello-swarm/workspace/"
Enter
Sleep 3s

# Step 6: Show logs
Type "swarmkit logs examples/hello-swarm/workspace/ --last 1"
Enter
Sleep 3s

# Done
Type "# That's it. YAML in, agents out."
Enter
Sleep 3s
