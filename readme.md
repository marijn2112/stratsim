# Python-based Reinforcement learning (RFL) vs rule-based agent comparison turn-based strategy game
This file intends to clarify the structure and actions going on in the stratsim.py file. The .py file has lots of comments and some elements in this explanation are also covered in the report, but they are reiterated here for clarity.

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

For repeated runs, only parts 2-4 are completed and the best agent is kept in memory until the set amount of runs is complete, only then are its weights saved in step 5.

Of the parts of the loop, the elements under agent control all fall within the same for loop, this means the agents can use agent-specific variables for all actions without storing them in an agent-specific way. Before that loop the agents are looped through to store information in dictionaries/lists, and after it conflicts are ended when needed to ensure all agents have had a chance to act.

Notation-wise notably a few times "civ" is as abbreviation for economic industry (civilian) and the name tile and province are also used interchangably for the smallest map unit. In hindsight inconvenient, map-elements have IDs starting from 1 while they are naturally indexed starting at 0.

# Configurable variables
Important variables that can be tested with different values are at the top of their related sections, except the performance-related variables which are at the top of the file.

# Created files
This section briefly explains all files the code can produce, depending on set variables, all of these files are put in a subfolder named output_files at the location of where this file is run:

- reward_function.txt displays on each line the highest rfl reward score found thus far for a run of the game
- best_weight_values.txt displays the best performing weights as found thus far

with disable_log set to False (all files relate only to the most recent run of the game):
- buildings_map_turn_*n*.xlsx displays the amount of buildings in each state at turn *n*, the file shows a grid of all provinces, but as buildings are on state-level all provinces of each state contain the same value.
- control_map_turn_*n*.xlsx displays which agent controlled each tile  at turn *n*, this includes occupation during conflict.
- turn_log.txt stores the most notable events of each turn, including started and ended conflicts and the use of units by all agents.
- unit_map_turn_*n*.xlsx displays the amount of units present on each tile at turn *n*, due to l

# The rules of the rule-based agents
This section summarizes the logic set for the rule-based agents:
- They seek to maintain a ratio of 3:1 ratio of economic vs military buildings, if they are outnumbered by their enemies they will try to build defensive builds where possible and otherwise solely military buildings.
- The agent attempts to match the amount of neutral units on its borders, but it if it is at war it prioritizes having at least 80% of the amount of enemy units on its borders with them.
- The agent only declares war if it has more units than any enemies it has, plus an additional 5 units per enemy to prevent it from being too agressive when strong, in addition it avoids war if its neutral neighbors have a lot of units.

# Optimization of reinforcement learning-based agents
The weights of the reinforcement learning agents are learned as follows:
- At the start of each simulated game, the weights previously appearing best are loaded from a file if it is available, if not the memory is checked for any best agent from previous runs, if that is also absent agent weights are randomly generated.
- Performance of the agents using reinforcement learning for their behavior is evaluated when the turn limit is reached or the agent loses all territory, in either case, the reward function is the sum of the amount of turns the agent "survived" (fairly small contribution) and the amount of industry the agent accumulated, with sufficient game length, all states will have the maximum amount of buildings and so this is mainly an indicator of conquered/lost territory.
- At the end of the game, the weights of the agent with the best score from the reward function will be kept, in all runs after the first one, this best weight stored in memory is used for the next game with a random weight is modified differently for each RFL agent.
- If the set amount of runs has been reached, the best observed weights are stored in a file to be further modified in further runs of the .py file.
