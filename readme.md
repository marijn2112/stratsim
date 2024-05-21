# Reinforcement learning vs rule-based agent comparison game
This file intends to clarify the structure and actions going on in the stratsim.py file. The file has lots of comments and some elements are also covered in the report, but this reiterates the most important things.

# Requirements
Only the plugins imported on top of the file are necessary, with the exception that Pandas may need to have "openpyxl" installed to create the output Excel files as is noted in the imports section.

# Structure
The code file is structured as follows:
1. Plugin imports
2. Map class definitions and generation
3. Agent class definition, setup (including RFL weights) and resource allocation
4. Action loop:
	1. Resetting/recalculation of variables used throughout the turn, such as whether units have been used, application of unit healing/damage based on the amount of industry the agents have compared to their industry.
	2. Apply or count down to occupation of tiles that have enemy units in them.
	3. Agents with an empty building/unit queue can pick something to add to it.
	4. War declaring, first units evaluate their relative strength and then take action based on this.
	5. Unit movement
	6. Peace considerations
	7. Creation/updating of files that keep track of the events of the simulated game
5. Best RFL weight so far saved to a file

Of the parts of the loop, the enemy actions except peace all fall within the same for loop, this means the agents can use agent-specific variables for all actions without storing them in an agent-specific way. Before that loop the agents are looped through to store information in dictionaries/lists.

Notably a few times I use "civ" as abbreviation for economic industry (civilian) and the name tile and province are also used interchangably.

# Configurable variables
Important variables that can be tested with different values are at the top of their related sections, except the performance-related variables which are at the top of the file.

# The rules of the rule-based agents
This section summarizes the logic set for the rule-based agents:
- They seek to maintain a ratio of 3:1 ratio of economic vs military buildings, if they are outnumbered by their enemies they will try to build defensive builds where possible and otherwise solely military buildings.
- The agent attempts to match the amount of neutral units on its borders, but it if it is at war it prioritizes having at least 80% of the amount of enemy units on its borders with them.
- The agent only declares war if it has more units than any enemies it has, plus an additional 5 units per enemy to prevent it from being too agressive when strong, in addition it avoids war if its neutral neighbors have a lot of units.