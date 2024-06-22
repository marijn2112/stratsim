import os
import math
import random
import pandas as pd
#use "pip install openpyxl" in console if the preview saving to excel does not work, importing it should not be necessary
from time import process_time

output_directory = os.path.dirname(os.path.abspath(__file__))+ "\output_files"	#save output files to the subfolder of the one this file was placed in
if not os.path.exists(output_directory):
	os.makedirs(output_directory)
os.chdir(output_directory)



#performance settings, of these only the turn limit and amount of verification runs should impact the weight values directly
output_file_interval = 150														#because creating output files has a substantial performance impact, its frequency can be altered here
disable_log = True																#disable all non-essential output files to save performance and prevent unnecessary disk operations when doing > 1 runs
turns_limit = 600																#late in the simulation, there maybe a stalemate with many units on the map, and the program will become slower and slower
run_count = 10																	#how many times to run the game to tweak the RFL agent weights, only after the first run which re-generates the baseline score the agent weights are slowly randomized
verification_runs = 2															#to reduce the impact of agents getting lucky, if set >0, agent performance is averaged over 1+arg runs, decreasing learning speed but hopefully making the learnt weights genuinly better more often



open('reward_function.txt', 'w').close()
if run_count > 1:
	disable_log = True
change_weight_divisor = verification_runs + 1

while run_count > 0:
	run_count -= 1
	start_time = process_time()
	### Generate map
	# Tiles are generated that units are placed on, while buildings are in the states which encompass several tiles

	print("Generating map, {} runs remaining".format(run_count))

	mapwidth = 12	#must be a multiple of 3 to form states
	mapheight = 8	#must be a multiple of 2 to form states

	class Tile:
		def __init__(self, id, x, y):
			self.id = id
			self.x = x
			self.y = y
			self.neighbors = []
			self.controller = None
			self.occupation_counter = None			#tiles of states with defensive buildings take longer become controlled by the enemy even if no units are present
			self.occupation_timer_agent = None		#to not have different enemies use the same timer, save the agent ID
		def distance_to(self, other_tile_id):
			if self.y == provinces[other_tile_id-1].y:
				distance = abs(self.x-provinces[other_tile_id-1].x)
			elif self.x == provinces[other_tile_id-1].x:
				distance = abs(self.y-provinces[other_tile_id-1].y)
			else:
				distance = 0
				curr_tile = self
				while curr_tile != provinces[other_tile_id-1]:
					distance += 1
					shortest_distance = None
					closest_tile = None
					for neighbor in curr_tile.neighbors:	#instead of doing tricky distance maths since these are hexagons, there is a search from tile to tile, but only through the closest neighbor
						own_distance = abs(provinces[neighbor-1].y-provinces[other_tile_id-1].y) + abs(provinces[neighbor-1].x-provinces[other_tile_id-1].x)
						if shortest_distance == None or own_distance < shortest_distance:
							shortest_distance = own_distance
							closest_tile = neighbor
					curr_tile = provinces[closest_tile-1]
			return distance

	class State:
		def __init__(self, id, tiles):
			self.id = id
			self.tiles = tiles
			self.owner = None
			self.impassable = None
			self.neighbors_state = []				#could be inferred from tiles, but would be inefficient to do repeatedly
			self.buildings = {'military': 0, 'economic': 0, 'defensive': 0}

	provinces = []
	for y in range(1, (mapheight+1), 1):
		for x in range(1, (mapwidth+1), 1):
			provinces.append(Tile((x+(y-1)*mapwidth), x, y))	#id is the x value+(y-1)*mapwidth

	for province in provinces:
		for province2 in provinces:
			if province.x == province2.x and province.y == province2.y:	#same province
				pass
			else:
				if province.y == province2.y and abs(province.x-province2.x) == 1:
					province.neighbors.append(province2.id)
				else:
					#since its a hexagonal grid, the neighbor X/Y are not always the same as is represented with the "upside down" states
					if province.y % 2 == 0:
						if abs(province.y - province2.y) == 1 and (province.x == province2.x or (province.x-1) == province2.x):
							province.neighbors.append(province2.id)
					else:
						if abs(province.y - province2.y) == 1 and (province.x == province2.x or (province.x+1) == province2.x):
							province.neighbors.append(province2.id)

	states = []
	for i in range(1, int((mapwidth*mapheight)/3 + 1), 1):
		#states will contain the tile IDs of (x, y)(x+1, y)(x, y+1) OR (x, y)(x-1, y+1)(x, y+1)
		tiles = []
		row = math.ceil(i / (mapwidth / 1.5)) * 2	#states are spread over 2 rows, so row 2-4-6-8....
		row2 = row/2 - 1							#this changes the row to an offset

		origin_tile = int(math.floor(i * 1.5) + row2*mapwidth)

		if i%2 == 0:	#every second state is "upside down"
			tiles.append(origin_tile)
			tiles.append(origin_tile+mapwidth-1)
			tiles.append(origin_tile+mapwidth)
			states.append(State(i, tiles))
		else:
			tiles.append(origin_tile)
			tiles.append(origin_tile+1)
			tiles.append(origin_tile+mapwidth)
			states.append(State(i, tiles))

	for state in states:												#generate state neighbors attribute
		neighbor_tiles = []
		for tile in state.tiles:
			for neighbor_tile in provinces[tile-1].neighbors:
				neighbor_tiles.append(neighbor_tile)
		for state2 in states:
			if state.id != state2.id:
				if set(state2.tiles) & set(neighbor_tiles) != set():	#check intersection of the state's known neighbor tiles and the tiles a different state contains to infer neighboring
					state.neighbors_state.append(state2.id)

	def get_tile_reach(tile_id, own_tiles):								#search recursively for all tiles that can be reached for a certain tile
		reachable_set = set()
		def search_loop(input_tile):
			for path_tile in provinces[input_tile-1].neighbors:
				if (path_tile in own_tiles) and (path_tile not in reachable_set):
					reachable_set.add(path_tile)
					search_loop(path_tile)
		search_loop(tile_id)
		return reachable_set

	### Generate agents

	print("Dividing map, initializing agents")

	agent_count = 6						#among how many agents is the map divided

	extra_ai_type_reinforcement = 0		#how many agents over 50% should use RFL, will automatically be lowered if set higher than is possibly to apply

	#the following variables lower the minimum amount of this type an agent should have, the rest is randomly divided
	base_strength_offset = 1	#independent from mapsize, this offsets the resources of all agents, because agents without resources shouldnt exist, the number without random element below will be 3+this offset
	strength_difference = 0		#to allow a varying amount of resources per agent
	size_difference = 0			#to allow a varying amount of states per agent

	assignable_states_idx = list(range(len(states)))
	min_size = (len(states) // agent_count) - size_difference #floor division - strength_difference is minimum strength
	agent_sizes = [min_size for i in range(agent_count)]
	remainder = len(states) - sum(agent_sizes)

	while remainder > 0:
		idx_to_boost = random.randrange(len(agent_sizes))
		agent_sizes[idx_to_boost] += 1
		remainder -= 1

	agent_bonus_strengths = [base_strength_offset+random.randrange(strength_difference+1) for i in range(agent_count)]

	#building cost and types
	economic_output = 10	#each economic building generates by default 10 production per turn
	building_costs = {'economic': 100, 'military': 100, 'defensive': 70}

	ai_types = []
	rfl_agents = min(agent_count//2+extra_ai_type_reinforcement, agent_count)
	rfl_indexes = random.sample(range(agent_count), rfl_agents)
	for i in range(agent_count):
		if i in rfl_indexes:
			ai_types.append('rfl')
		else:
			ai_types.append('rule')

	class Agent:
		def __init__(self, id, action_type):
			self.id = id
			self.is_active = True
			self.action_type = action_type
			self.units = []
			self.weights = {}					#only used on RFL agents
			self.rfl_score = None				#only used on RFL agents
			self.unit_queue = {}				#dictionary that contains [unit_type, location, remaining_cost]
			self.building_queue = {}			#dictionary that contains [building_type, location, remaining_cost]
			self.enemies = {}					#dictionary that contains [enemy id: conflict duration]
			self.available_mil_production = 0	#determined at the start of each turn to compute amount of unit building progress, if too low don't construct and add to maintenance at all
			self.available_civ_production = 0	#determined at the start of each turn to compute amount of construction progress
		
		def get_controlled_enemy_tiles(self):	#used to be an attribute, but inconvenient to copy each time province control is adjusted
			controlled_tiles = []
			for tile in provinces:
				if tile.controller == self.id:
					controlled_tiles.append(tile.id)
			return controlled_tiles
		
		def get_unit_locations(self):
			unit_locations = []
			for unit in self.units:
				unit_locations.append(unit.location)
			return unit_locations
		
		def get_controlled_tiles(self):
			controlled_tiles = self.get_controlled_enemy_tiles()
			for state in states:
				if state.owner == self.id:
					for tile in state.tiles:
						if provinces[tile-1].controller == None or provinces[tile-1].controller == self.id:
							controlled_tiles.append(tile)
			return controlled_tiles
		
		def calculate_reward(self):				#when agent set inactive/set reward
			survive_score = turn_n // 3			#up to sim length/3 score for surviving
			industry_score = 0
			for state in states:
				if state.owner == agent.id:
					industry_score += state.buildings['economic'] + state.buildings['military']
			industry_score = industry_score * 5	#at least building cap*6*5~ score for buildings
			self.rfl_score = survive_score + industry_score

	agents = []
	for agent_id in range(agent_count):
		states_to_assign = agent_sizes[agent_id]
		assigned_idx = []
		for idx, potential_state in enumerate(states):
			if not states_to_assign > 0:
				break
			if potential_state.owner == None:
				potential_state.owner = agent_id
				assigned_idx.append(idx)
				#print("Agent ", agent_id, " has claimed state ", potential_state.id)
				states_to_assign -= 1
				if not states_to_assign > 0:
					break
				for neighbor in potential_state.neighbors_state:	#prioritize states neighboring the already assigned state, could be made recursive to a limited degree
					if states[neighbor-1].owner == None:
						states[neighbor-1].owner = agent_id
						assigned_idx.append(idx)
						#print("Agent ", agent_id, " has claimed state ", states[neighbor-1].id, " ,it was prioritized as a neighbor of state", potential_state.id)
						states_to_assign -= 1
						if not states_to_assign > 0:
							break
		agents.append(Agent(agent_id, ai_types[agent_id]))

		##With states assigned for this agent, its resources can be spread across these
		#Start by assigning resources that all agents posses, 2 economic and 1 military
		base_resource_locations = random.sample(assigned_idx, 3)
		states[base_resource_locations[0]].buildings['military'] += 1
		states[base_resource_locations[1]].buildings['economic'] += 1
		states[base_resource_locations[2]].buildings['economic'] += 1

		for i in range(agent_bonus_strengths[agent_id]):
			states[random.choice(assigned_idx)].buildings[random.choice(list(building_costs.keys()))] += 1

	weights_names_list = ['mil_base', 'civ_base', 'defense_base', 'mil_industry_ratio', 'civ_industry_ratio', 'defense_industry_ratio', 'mil_civ_existent', 'civ_civ_existent', 'defense_civ_existent', 'mil_mil_existent', 'civ_mil_existent', 'defense_mil_existent',	#building weights
							 'base_ranged_prio', 'base_melee_prio', 'unit_ratio_ranged', 'unit_ratio_melee', 'ranged_built_ranged_prio', 'ranged_built_melee_prio', 'melee_built_ranged_prio', 'melee_built_melee_prio', 												#unit building weights
							 'enemy_units_weight', 'enemies_weight', 'base_conflict', 'neutral_units_weight', 'own_units_weight', 'melee_own_units_prio', 'ranged_own_units_prio']																						#war declaring weights

	weight_to_change_idx = random.randrange(0, len(weights_names_list))

	rlf_agent_idx = 0
	for agent in agents:
		if agent.action_type == "rfl":
			if 'prev_weights_used' in globals():
				print("Applying same weights as last run to verify observed reward scores")
				agent.weights = dict(zip(weights_names_list, prev_weights_used[rlf_agent_idx]))
			elif 'best_rfl_agent' in globals():
				print("Applying previous best agent from memory")
				agent.weights = best_rfl_agent.weights
				agent.weights[weights_names_list[weight_to_change_idx]] += random.uniform(-5, 5) * max(1 - turn_n/turns_limit, 0.1)		#last part can be commented out, used to gradually decrease size of weight modification
			elif os.path.isfile("best_weight_values.txt"):
				print("Applying weights from best weights file")
				with open('best_weight_values.txt') as weight_file:
					weight_values = weight_file.read().splitlines()
				agent.weights = dict(zip(weights_names_list, [float(weight) for weight in weight_values]))
			else:
				print("Generating random weights")
				for key in weights_names_list:
					agent.weights[key] = random.uniform(-1, 1)
			rlf_agent_idx += 1

	### Gameplay data

	mil_output = 10		#by default, a military building provides 10 maintenance or unit cost
	class Unit:
		types = {	#could be expanded during the game, this dictionary is shared across all objects, because of the queue's nature
			'infantry': {'maintenance': 4, 'cost': 70, 'attack': 25, 'defense': 20, 'movement': 2, 'range': 1, 'hp_base': 60},
			'bowman': {'maintenance': 5, 'cost': 90, 'attack': 15, 'defense': 5, 'movement': 2, 'range': 2, 'hp_base': 50},
			#'artillery': {'maintenance': 5, 'cost': 60, 'attack': 35, 'defend': 0, 'movement': 1, 'range': 2, 'hp_base': 40}
		}
		def __init__(self, type, location):
			self.type = type
			self.hp = None
			self.location = location
			self.remaining_movement = 0		#is reset every turn
			self.has_attacked = False
		def turn_reset_attribute(self):
			self.has_attacked = False		#if pushed into enemy controlled tiles OR attacked enemy unit, no more actions possible
			self.remaining_movement = Unit.types[self.type]['movement']
		def is_ranged(self):
			if Unit.types[self.type]['range'] > 1:
				return True
			return False
		def movetowards(self, target_tile, controlled_tiles, enemy_locations):
			while self.remaining_movement > 0:
				shortest_distance = None
				best_target = None
				#print(self.location)
				for neighbor in provinces[self.location-1].neighbors:
					if neighbor not in controlled_tiles:
						continue
					if shortest_distance == None or provinces[neighbor-1].distance_to(target_tile) < shortest_distance:
						shortest_distance = provinces[neighbor-1].distance_to(target_tile)
						best_target = neighbor
				if (best_target not in enemy_locations) and (best_target != None):
					#event_list.append("unit moved from tile {} to tile {} towards tile {} by agent {}, distance to target {}, with considered tiles {}".format(self.location, best_target, target_tile, agent.id, shortest_distance, provinces[self.location-1].neighbors))
					self.location = best_target
					self.remaining_movement -= 1
				else:
					break
		def ranged_attack(self, target_tile, target_agents):
			attack_damage = Unit.types[self.type]['attack']//(Unit.types[self.type]['hp_base']/self.hp)
			unit_count = 0
			for agent_idx in target_agents:
				unit_count += agents[agent_idx].get_unit_locations().count(target_tile)
			if unit_count != 0:
				damage_per_unit = attack_damage // unit_count
				for enemy_idx in target_agents:
					for unit in agents[enemy_idx].units:
						if unit.location == target_tile:
							unit.hp -= damage_per_unit
							if unit.hp <= 0:
								agents[enemy_idx].units.remove(unit)
								event_list.append("Ranged unit inflicted a fatal {} damage on a unit on tile {} from tile {}, out of maximum {} damage".format(damage_per_unit, target_tile, self.location, attack_damage))
			
	def attack_tile(agent_self, agent_enemies, location_from, location_to, unit_amount):
		enemy_unit_count = 0
		unit_indexes = []
		total_attack_value = 0
		enemy_counter_attack_value = 0
		for state in states:
			if location_to in state.tiles:
				fort_value = state.buildings['defensive']
		for unit_idx, unit in enumerate(agents[agent_self].units):
			if unit.location == location_from:
				unit_indexes.append(unit_idx)
				total_attack_value += Unit.types[unit.type]['attack']//(Unit.types[unit.type]['hp_base']/unit.hp)
			if len(unit_indexes) >= unit_amount:
				break
		for agent_idx in agent_enemies:
			enemy_unit_count += agents[agent_idx].get_unit_locations().count(location_to)
			for enemy_unit in agents[agent_idx].units:
				if enemy_unit.location == location_to:
					enemy_counter_attack_value += Unit.types[enemy_unit.type]['attack']//(Unit.types[enemy_unit.type]['hp_base']/enemy_unit.hp)
		if enemy_unit_count > 0:																		#if all units stopped existing from previous attacks, skip the attack/defense calculations
			attack_per_enemy_unit = (enemy_unit_count // enemy_unit_count)*(1 - (fort_value*0.1))		#up to 50% damage reduction if 5 levels of fort are level in the state
			counter_attack_per_friendly_unit = enemy_counter_attack_value // unit_amount
			for enemy_idx in agent_enemies:
				for unit in agents[enemy_idx].units:
					if unit.location == location_to:
						unit.hp -= attack_per_enemy_unit*(1-(Unit.types[unit.type]['defense']/50))		#defense functions as 2% damage reduction per point
						if unit.hp <= 0:
							agents[enemy_idx].units.remove(unit)
							enemy_unit_count -= 1
		else:
			counter_attack_per_friendly_unit = 0
		for unit in sorted(unit_indexes, reverse=True):													#as unit indexes are used, evaluate and remove if necessary by index in reverse order
			agents[agent_self].units[unit].hp -= counter_attack_per_friendly_unit
			agents[agent_self].units[unit].has_attacked = True
			if agents[agent_self].units[unit].hp <= 0:
				agents[agent_self].units.pop(unit)
			elif enemy_unit_count == 0:
				agents[agent_self].units[unit].location = location_to
		

	### Turn actions

	open('turn_log.txt', 'w').close()

	turn_n = 0
	while len(agents) > 1:
		turn_n += 1

		if turn_n % 100 == 0:
			print("Starting turn ", turn_n)

		event_list = []

		for agent in agents:

			if agent.is_active == False:
				continue

			#Reset unit use variables
			for unit in agent.units:
				unit.turn_reset_attribute()

			#Increment conflict duration
			for enemy_id in agent.enemies.keys():
				agent.enemies[enemy_id] += 1

			#See if industry can support units and apply damage and healing based off that, also store industry variables for later
			mil_count = 0
			civ_count = 0
			total_maintenance = 0

			for unit in agent.units:
				total_maintenance += Unit.types[unit.type]['maintenance']

			provinces_of_owned_states = []
			for state in states:
				if state.owner == agent.id:
					for tile in state.tiles:
						provinces_of_owned_states.append(tile)

			provinces_controlled_by_enemies = []
			provinces_controlled_by_self = []
			for tile in provinces:
				if tile in provinces_of_owned_states:
					if tile.controller != None and tile.controller != agent.id:
						provinces_controlled_by_enemies.append(tile.id)
				else:
					if tile.controller == agent.id:
						provinces_controlled_by_self.append(tile.id)

			for state in states:
				state_controlled_by_enemy = False
				enemy_state_controlled_by_self = False
				if len(set(state.tiles) & set(provinces_controlled_by_enemies)) > 1:
					state_controlled_by_enemy = True
				if len(set(state.tiles) & set(provinces_controlled_by_self)) > 1:
					enemy_state_controlled_by_self = True
				if state.owner == agent.id and not state_controlled_by_enemy:
					civ_count += state.buildings['economic']
					mil_count += state.buildings['military']
				elif state.owner != agent.id and enemy_state_controlled_by_self:
					civ_count += state.buildings['economic']
					mil_count += state.buildings['military']
			
			mil_production = mil_count*mil_output-(total_maintenance)

			agent.available_civ_production = civ_count * economic_output
			agent.available_mil_production = max(mil_production, 0)

			if mil_production < 0:
				relative_deficit = max(mil_production/len(agent.units)/(total_maintenance/len(agent.units)), 1)			#compute what share of maintanence falls unsupported
				for idx, unit in enumerate(agent.units):
					unit.hp -= Unit.types[unit.type]['hp_base']*relative_deficit*0.075										#give at most 7,5% damage per turn for having no industry supporting a unit
					if unit.hp <= 0:
						agent.units.pop(idx)
						#recalculate deficit if a unit ceases to exist, to prevent all units ceasing to exist from a slight deficit
						total_maintenance -= Unit.types[unit.type]['maintenance']
						mil_production += Unit.types[unit.type]['maintenance']
						if mil_production >= 0 or len(agent.units) == 0:
							break
						relative_deficit = max(mil_production/len(agent.units)/(total_maintenance/len(agent.units)), 1)
			else:
				for unit in agent.units:
					if unit.location in provinces_of_owned_states:
						if unit.location not in provinces_controlled_by_enemies:
							unit.hp = min(Unit.types[unit.type]['hp_base'], unit.hp+Unit.types[unit.type]['hp_base']*0.05)	#if there is any industry surplus, allow units to heal 5% per turn on friendly territory, this value must be significantly less than can be inflicted by an enemy
		
		#by applying occupation last and in a new loop, production of occupied states is not yet counted for production
		for agent in agents:
			if agent.is_active == False:
				continue
			unit_locations = []
			for unit in agent.units:
				unit_locations.append(unit.location)
			for state in states:															#by having the outer loop be the state, the defensive building tier can be accessed easily
				for tile in state.tiles:
					if tile in unit_locations:
						if (state.owner in agent.enemies.keys() and provinces[tile - 1].controller != agent.id) or (state.owner == agent.id and provinces[tile-1].controller != None and provinces[tile-1].controller != agent.id):	#make sure the tile wasnt already controlled, also that units in now neutral (previously hostile) tiles don't occupy
							if state.buildings['defensive'] == 0:								#if region has no defensive building, set controller immediately
								provinces[tile - 1].controller = agent.id
								event_list.append("Tile {} has been occupied by agent {}".format(tile, agent.id))
							elif provinces[tile - 1].occupation_timer_agent == agent.id:		#if region is counting down towards occupation by current agent, reduce the count
								provinces[tile - 1].occupation_counter -= 1
								if provinces[tile - 1].occupation_counter < 1:
									provinces[tile - 1].controller = agent.id
									event_list.append("Tile {} has been occupied by agent {} despite its defenses".format(tile, agent.id))
							else:																#otherwise, start timer
								provinces[tile - 1].occupation_timer_agent = agent.id
								provinces[tile - 1].occupation_counter = state.buildings['defensive']

		#finish queued actions, place buildings/units that were queued for construction
		for agent in agents:
			if agent.is_active == False:
				continue
			provinces_of_owned_states = []																		#queued units use this value, compute it before the loop
			for state in states:
				if state.owner == agent.id:
					for tile in state.tiles:
						provinces_of_owned_states.append(tile)
			provinces_occupied_by_enemies = []
			for otheragent in agents:
				if otheragent.id != agent.id:
					for tile in otheragent.get_unit_locations():
						provinces_occupied_by_enemies.append(tile)

			provinces_controlled_by_enemies = []																#buildings only stop functioning when control is seized...
			provinces_controlled_by_self = []
			for tile in provinces:
				if tile in provinces_of_owned_states:
					if tile.controller != None and tile.controller != agent.id:
						provinces_controlled_by_enemies.append(tile.id)
					else:
						if tile.controller == agent.id or tile.controller == None:
							provinces_controlled_by_self.append(tile.id)
			
			unit_deployable_tiles = set(provinces_of_owned_states) - set(provinces_occupied_by_enemies) - set(provinces_controlled_by_enemies)		#...while units cannot deploy in areas with any enemy presence either

			if "unit_type" in agent.unit_queue.keys():														#unit in queue
				if agent.unit_queue['location'] in unit_deployable_tiles:
					if agent.unit_queue['remaining_cost'] < 1:
						agent.units.append(Unit(agent.unit_queue['unit_type'], agent.unit_queue['location']))
						agent.units[-1].hp = Unit.types[agent.units[-1].type]['hp_base'] / 2				#avoid deploying freshly trained divisions right onto the frontline being advantagious
						agent.unit_queue = {}																#clear dictionary
					else:
						agent.unit_queue['remaining_cost'] -= agent.available_mil_production
				elif len(unit_deployable_tiles) > 0:														#move queued units that are no longer in a valid location
					agent.unit_queue['location'] = random.choice(list(unit_deployable_tiles))

			if "building_type" in agent.building_queue.keys():												#building in queue
				state_controlled_by_enemy = False
				enemy_state_controlled_by_self = False
				provinces_of_location = states[agent.building_queue['location'] - 1].tiles
				if len(set(provinces_of_location) & set(provinces_controlled_by_enemies)) > 1:
					state_controlled_by_enemy = True
				if len(set(provinces_of_location) & set(provinces_controlled_by_self)) > 1:
					enemy_state_controlled_by_self = True

				if states[agent.building_queue['location'] - 1].owner != agent.id and not enemy_state_controlled_by_self:		#remove queued buildings that are no longer in a valid location
					agent.building_queue = {}																		#clear dictionary when invalid
				elif not state_controlled_by_enemy:																	#building doesn't progress if more than halve the state is occupied, simply do not progress in that case
					if agent.building_queue['remaining_cost'] < 1:
						states[agent.building_queue['location'] - 1].buildings[agent.building_queue['building_type']] += 1				#construct building
						agent.building_queue = {}																	#clear dictionary when item constructed
					else:
						agent.building_queue['remaining_cost'] -= agent.available_civ_production


		#after completing the industry and occupation checks/queue for all agents, let agents act

		controlled_tiles_dict = {}												#this dictionary lists the tiles of each agent
		tiles_by_controller_dict = {}											#this dictionary lists the agent of each tile
		for agent in agents:
			controlled_tiles_dict[agent.id] = agent.get_controlled_tiles()
			for tile in agent.get_controlled_tiles():
				tiles_by_controller_dict[tile] = agent.id

		for agent in agents:
			if agent.is_active == False:
				continue
			#copy of queue section, multiple variables are useful so making this a function _might_ be impractical
			provinces_of_owned_states = []
			for state in states:
				if state.owner == agent.id:
					for tile in state.tiles:
						provinces_of_owned_states.append(tile)
			provinces_occupied_by_enemies = []
			for otheragent in agents:
				if otheragent.id != agent.id:
					for tile in otheragent.get_unit_locations():
						provinces_occupied_by_enemies.append(tile)

			provinces_controlled_by_enemies = []
			provinces_controlled_by_self = []
			for tile in provinces:
				if tile in provinces_of_owned_states:
					if tile.controller != None and tile.controller != agent.id:
						provinces_controlled_by_enemies.append(tile.id)
					else:
						if tile.controller == agent.id or tile.controller == None:
							provinces_controlled_by_self.append(tile.id)
			
			unit_deployable_tiles = list(set(provinces_of_owned_states) - set(provinces_occupied_by_enemies) - set(provinces_controlled_by_enemies))

			outnumbered_by_enemies = False
			enemy_strength_estimate = 0
			own_strength = len(agent.units)
			for enemyid in agent.enemies.keys():
				enemy_strength_estimate += len(agents[enemyid].units)
			if enemy_strength_estimate > own_strength:
				outnumbered_by_enemies = True

			if agent.building_queue == {}:																		#choose building to queue
				#information gathering
				pre_cost_mil_total = 0
				civ_total_industry = agent.available_civ_production / economic_output
				for state in states:
					state_controlled_by_enemy = False
					enemy_state_controlled_by_self = False
					if len(set(state.tiles) & set(provinces_controlled_by_enemies)) > 1:
						state_controlled_by_enemy = True
					if len(set(state.tiles) & set(provinces_controlled_by_self)) > 1:
						enemy_state_controlled_by_self = True
					if state.owner == agent.id and not state_controlled_by_enemy:
						pre_cost_mil_total += state.buildings['military']
					elif state.owner != agent.id and enemy_state_controlled_by_self:
						pre_cost_mil_total += state.buildings['military']										#the military production value stored in the agent has maintanence removed
					if pre_cost_mil_total != 0:
						civ_to_mil_ratio = civ_total_industry / pre_cost_mil_total
					else:
						civ_to_mil_ratio = 999

				normal_building_allowed_states = []
				defensive_building_allowed_states = []
				for state in states:																			#TODO could evaluate state value, then use that to consider especially defensive building placement
					if state.owner == agent.id:
						if len(set(state.tiles) & set(provinces_controlled_by_enemies)) < 1:					#do not allow defensive buildings in a partially occupied state
							if state.buildings['military'] + state.buildings['economic'] > 0 and state.buildings['defensive'] < 5:					#do not allow defensive buildings in a state of no value
								defensive_building_allowed_states.append(state.id)
						if len(set(state.tiles) & set(provinces_controlled_by_enemies)) < 2 and (state.buildings['military'] + state.buildings['economic']) < 20:
							normal_building_allowed_states.append(state.id)
				
				##Actual action
				if len(normal_building_allowed_states) > 0:
					has_building_slots = True
				else:
					has_building_slots = False
				if agent.action_type == "rfl" and has_building_slots:											#if there is no possibility to choose, go straight to fort-building
					military_priority = agent.weights['mil_base'] + (civ_to_mil_ratio * agent.weights['mil_industry_ratio']) + (civ_total_industry * agent.weights['mil_civ_existent']) + (pre_cost_mil_total * agent.weights['mil_mil_existent'])
					economic_priority = agent.weights['civ_base'] + (civ_to_mil_ratio * agent.weights['civ_industry_ratio']) + (civ_total_industry * agent.weights['civ_civ_existent']) + (pre_cost_mil_total * agent.weights['civ_mil_existent'])
					defensive_priority=agent.weights['defense_base']+(civ_to_mil_ratio*agent.weights['defense_industry_ratio'])+(civ_total_industry*agent.weights['defense_civ_existent'])+(pre_cost_mil_total*agent.weights['defense_mil_existent'])
					if military_priority > max(economic_priority, economic_priority):
						produce_building = "mil"
					elif economic_priority > defensive_priority:
						produce_building = "civ"
					else:
						produce_building = "defense"
				else:
					produce_building = None
				#Building queuing - mixed rule-based conditions and RFL based choice from above
				if (agent.available_mil_production == 0 or produce_building == "mil") and has_building_slots:	#make sure the agent doesnt queue units when having little production remaining, otherwise this will be true often and itll never prioritize the long term
					agent.building_queue = {'building_type': 'military', 'location': random.choice(normal_building_allowed_states), 'remaining_cost': building_costs['military']}
				elif outnumbered_by_enemies or not has_building_slots or produce_building == "defense":
					if len(defensive_building_allowed_states) > 0:
						agent.building_queue = {'building_type': 'defensive', 'location': random.choice(defensive_building_allowed_states), 'remaining_cost': building_costs['defensive']}
					elif has_building_slots:
						agent.building_queue = {'building_type': 'military', 'location': random.choice(normal_building_allowed_states), 'remaining_cost': building_costs['military']}
				elif civ_to_mil_ratio < 3 or produce_building == "civ":
					agent.building_queue = {'building_type': 'economic', 'location': random.choice(normal_building_allowed_states), 'remaining_cost': building_costs['economic']}
				else:
					agent.building_queue = {'building_type': 'military', 'location': random.choice(normal_building_allowed_states), 'remaining_cost': building_costs['military']}

			if agent.unit_queue == {} and unit_deployable_tiles != []:											#choose unit to queue, regardless of action type, do not allow agents to build more than they can support
				if agent.available_mil_production > 2:
					#information gathering
					unit_type_ratio = 1

					ranged_units = 0
					melee_units = 0
					for unit in agent.units:
						if unit.is_ranged():
							ranged_units += 1
						else:
							melee_units += 1
					if ranged_units != 0:
						unit_type_ratio = melee_units / ranged_units

					
					#actual action
					if agent.action_type == "rfl":
						unit_ranged_prio = agent.weights['base_ranged_prio'] + ranged_units * agent.weights['ranged_built_ranged_prio'] + melee_units * agent.weights['melee_built_ranged_prio'] + unit_type_ratio * agent.weights['unit_ratio_ranged'] + len(agent.units) * agent.weights['ranged_own_units_prio']
						unit_melee_prio = agent.weights['base_melee_prio']  + ranged_units * agent.weights['ranged_built_melee_prio'] + melee_units * agent.weights['melee_built_melee_prio'] + unit_type_ratio * agent.weights['unit_ratio_melee'] + len(agent.units) * agent.weights['melee_own_units_prio']
						if unit_melee_prio > unit_ranged_prio:
							agent.unit_queue = {'unit_type': 'infantry', 'location': random.choice(unit_deployable_tiles), 'remaining_cost': Unit.types['infantry']['cost']}
						else:
							agent.unit_queue = {'unit_type': 'bowman', 'location': random.choice(unit_deployable_tiles), 'remaining_cost': Unit.types['bowman']['cost']}
					else:
						if outnumbered_by_enemies:
							if unit_type_ratio < 3:																																		#if at peace or evenly matched with opponent, 3:1 ranged and melee
								agent.unit_queue = {'unit_type': 'infantry', 'location': random.choice(unit_deployable_tiles), 'remaining_cost': Unit.types['infantry']['cost']}
							else:
								agent.unit_queue = {'unit_type': 'bowman', 'location': random.choice(unit_deployable_tiles), 'remaining_cost': Unit.types['bowman']['cost']}
						else:
							agent.unit_queue = {'unit_type': 'infantry', 'location': random.choice(unit_deployable_tiles), 'remaining_cost': Unit.types['infantry']['cost']}			#if at numerical disadvantage against enemy, spam only melee
						#if at numerical advantage, could increase ranged-melee unit ratio
			###War declaring, information shared with unit movement so cannot be made conditional
			##Information gathering
			potential_enemies_dictionary = {}

			own_states = []
			own_tiles = []
			for state in states:
				controlled_provinces = 0
				if state.owner == agent.id:
					for tile in state.tiles:
						if provinces[tile - 1].controller == None or provinces[tile - 1].controller == agent.id:
							own_tiles.append(tile)																												#going to ignore units on tiles, and consider those in the pathing
							controlled_provinces+=1
					if controlled_provinces > 0:																												#any state that is NOT entirely enemy-occupied will be considered for bordering other agents
						own_states.append(state.id)
			
			#Determine tiles controlled by potential enemy, then find neighboring tiles in friendly territory to station units
			for enemy_state in states:
				if enemy_state.owner != agent.id:
					for potential_own_state in enemy_state.neighbors_state:
						if potential_own_state in own_states:
							if enemy_state.owner not in agent.enemies:
								if enemy_state.owner not in potential_enemies_dictionary.keys():
									potential_enemies_dictionary[enemy_state.owner] = {'units': len(agents[enemy_state.owner].units), 'neighbor_tiles': set()}		#could add assymetry #this line this nothing after the dictionary entry is generated for the first time this turn
								for tile in states[enemy_state.id - 1].tiles:																						#get tiles of the enemy state
									our_neighbors_of_enemy_tile = list(set(provinces[tile - 1].neighbors).intersection(own_tiles))
									#print('agent ', agent.id, 'claims that their state', potential_own_state, 'neighbors enemy province ', tile, ', part of state ', enemy_state.id, 'in provinces', our_neighbors_of_enemy_tile, 'of which ', provinces[tile - 1].neighbors, 'were the neighboring tiles considered')
									for neighbor_of_enemy_tile in our_neighbors_of_enemy_tile:
										potential_enemies_dictionary[enemy_state.owner]['neighbor_tiles'].add(neighbor_of_enemy_tile)
			
			curr_enemy_strength = 0
			for enemy_agent_id in agent.enemies:
				curr_enemy_strength += len(agents[enemy_agent_id].units)
			remaining_units = len(agent.units) - max(curr_enemy_strength, 5*len(agent.enemies))						#prevent agents attacking everyone at once

			neighbors_strength = 0
			for info in potential_enemies_dictionary.values():
				neighbors_strength += info['units']

			##Actual action
			enemy_target = None
			rfl_war = False
			if agent.action_type == "rfl":
				war_priority = agent.weights['base_conflict'] - len(agent.enemies) * agent.weights['enemies_weight'] - curr_enemy_strength * agent.weights['enemy_units_weight'] - neighbors_strength * agent.weights['neutral_units_weight'] + len(agent.units) * agent.weights['own_units_weight']
				conflict_threshold = 0		#abitrary value ideally to be around what could be achieved with the default weight values
				if war_priority > conflict_threshold:
					rfl_war = True

			if (remaining_units > neighbors_strength / 2 and remaining_units > 4 and not agent.action_type == "rfl") or rfl_war:
				best_score = 0
				for potential_target_id, info in potential_enemies_dictionary.items():
					if set(agents[potential_target_id].get_controlled_tiles()) & set(agent.get_unit_locations()):	#skip potential target if units in their territory
						continue
					if info['units'] < remaining_units:
						enemy_resources = 0
						enemy_states = 0
						enemy_defenses = 0
						enemy_units = len(agents[potential_target_id].units)
						for state in states:
							if state.owner == agent.id:
								enemy_states += 1
								enemy_resources += state.buildings['economic'] + state.buildings['military']
								enemy_defenses += state.buildings['defensive']
						enemy_enemies = len(agents[potential_target_id].enemies)
						if enemy_enemies <= enemy_states:															#if the target agent has more enemies than states, do not bother
							enemy_score = enemy_resources - enemy_defenses - enemy_units
							if enemy_score > best_score:
								enemy_target = potential_target_id
								best_score = enemy_score
			
			if enemy_target != None:
				agent.enemies[enemy_target] = 0
				agents[enemy_target].enemies[agent.id] = 0
				event_list.append("Agent {} declared war on agent {}, with units at {}".format(agent.id, enemy_target, agent.get_unit_locations()))

			###Unit movement
			#could be made into a while loop if the sequential movement of units causes problems
			#Should force occupation of a tile before moving further
			
			if len(agent.units) > 0:
				#for all agents, find what tiles units should be on to protect their boundry
				front_tiles_dictionary = {i: [] for i in range(agent_count)}
				visible_tiles_sets = {i: set() for i in range(agent_count)}
				neutral_agents = []

				for tile_id in controlled_tiles_dict[agent.id]:
					for neighbor_tile in provinces[tile_id-1].neighbors:
						if tiles_by_controller_dict[neighbor_tile] != agent.id:
							front_tiles_dictionary[tiles_by_controller_dict[neighbor_tile]].append(tile_id)
							visible_tiles_sets[tiles_by_controller_dict[neighbor_tile]].add(neighbor_tile)

				for neutral_id in front_tiles_dictionary.keys():
					if front_tiles_dictionary[neutral_id] != [] and neutral_id not in agent.enemies.keys():
						neutral_agents.append(neutral_id)

				#determine how many units should be on the border with each agent

				units_per_front = {}

				minimum_units_against_enemies = 0
				neutral_agent_strength = 0
				for neutral_agent in neutral_agents:
					neutral_agent_strength += len(agents[neutral_agent].units)
				for enemy in agent.enemies:		#first allocate units to fronts with enemies
					visible_units_of_enemy = 0
					enemy_unit_locations = agents[enemy].get_unit_locations()
					for unit_location in enemy_unit_locations:
						if unit_location in own_tiles:
							visible_units_of_enemy += 1
						elif unit_location in visible_tiles_sets[enemy]:
							visible_units_of_enemy += 1
					minimum_units_against_enemies += visible_units_of_enemy
				
				if len(agent.units) > minimum_units_against_enemies and agent.enemies != {}:
					visible_neutral_units = 0
					for other_agent in agents:
						if other_agent != agent.id and other_agent not in agent.enemies:
							neutral_unit_locations = other_agent.get_unit_locations()
							for unit_location in neutral_unit_locations:
								if unit_location in visible_tiles_sets[other_agent.id]:
									visible_neutral_units += 1
					units_against_enemies = max(minimum_units_against_enemies, min((curr_enemy_strength*0.8), len(agent.units)))			#assign at least [80% of enemy strength] units to enemies, or all units if that is impossible
					units_against_neutrals = min(len(agent.units) - units_against_enemies, visible_neutral_units)
					if (units_against_enemies + units_against_neutrals) < len(agent.units):													#if boundries can be guarded 1:1, put all remaining units towards battling enemy agents
						units_against_enemies += (len(agent.units) - units_against_enemies - units_against_neutrals)
					for enemy in agent.enemies:
						if len(agents[enemy].units) > 0:
							if curr_enemy_strength == 0:
								units_per_front[enemy] = units_against_enemies/len(agent.enemies)
							else:
								units_per_front[enemy] = units_against_enemies//(curr_enemy_strength/(len(agents[enemy].units)))
					for neutral_agent in neutral_agents:
						if len(agents[neutral_agent].units) > 0:
							units_per_front[neutral_agent] = units_against_neutrals//(neutral_agent_strength/len(agents[neutral_agent].units))
				elif agent.enemies == {}:
					for neutral_agent in neutral_agents:
						if len(agents[neutral_agent].units) > 0:
							units_per_front[neutral_agent] = len(agent.units)//(neutral_agent_strength/len(agents[neutral_agent].units))	#if no enemies, split units based on neighbor strength and not just their visible units
				else:
					for enemy in agent.enemies:
						units_per_front[enemy] = len(agent.units)//(curr_enemy_strength/max(1, len(agents[enemy].units)))

				#count units already on fronts, and exclude them from movement, in case of casualties must ensure that unit ratio matches between total army and front
				assignable_units = [i for i in range(len(agent.units))]
				for unit_idx in assignable_units:
					clear_from_list = False
					#units stuck in neutral territory, force to teleport 1 tile towards friendly territory
					if (tiles_by_controller_dict[agent.units[unit_idx].location] != agent) and (tiles_by_controller_dict[agent.units[unit_idx].location] not in agent.enemies.keys()):
						closest_friendly_tile = None
						closest_friendly_tile_distance = None
						for friendly_tile in controlled_tiles_dict[agent.id]:
							if closest_friendly_tile == None or provinces[agent.units[unit_idx].location-1].distance_to(friendly_tile) < closest_friendly_tile_distance:
								closest_friendly_tile = friendly_tile
								closest_friendly_tile_distance = provinces[agent.units[unit_idx].location-1].distance_to(friendly_tile)
						if closest_friendly_tile != None:
							best_target_tile = None
							best_target_distance = None
							for neighbor_tile in provinces[agent.units[unit_idx].location-1].neighbors:
								if best_target_tile == None or provinces[neighbor_tile-1].distance_to(closest_friendly_tile) < best_target_distance:
									best_target_tile = neighbor_tile
									best_target_distance = provinces[neighbor_tile-1].distance_to(closest_friendly_tile)
							agent.units[unit_idx].location = best_target_tile
						clear_from_list = True
					#normal check
					else:
						for front_id, front_tiles in front_tiles_dictionary.items():
							if agent.units[unit_idx].location in front_tiles or agent.units[unit_idx].location in visible_tiles_sets[front_id]:	#TODO ideally distinguish between unit types
								if front_id in units_per_front.keys():
									units_per_front[front_id] -= 1
									if units_per_front[front_id] >= 0:	#this number could go negative if too many units are on one line, in that case, do allow unit movement
										clear_from_list = True
					if clear_from_list:
						assignable_units.remove(unit_idx)

				#loop through units trying to move them to a location, if successful remove them from the list of units to move
				curr_locations = agent.get_unit_locations()
				enemy_locations = []
				for enemy in agent.enemies.keys():
					for enemy_unit in agents[enemy].units:
						enemy_locations.append(enemy_unit.location)
				for front_id, front_units in units_per_front.items():
					front_tiles = set(front_tiles_dictionary[front_id])
					front_tile_priority = {tile: front_tiles_dictionary[front_id].count(tile) for tile in front_tiles}
					front_tile_present_units = {tile: curr_locations.count(tile) for tile in front_tiles }
					#print(front_tile_present_units)
					iter = 0
					while iter < front_units//(len(front_tiles)+1):								#loop through the tiles multiple times, each time assigning 1 more units per tile where possible
						iter += 1
						for tile_id in front_tiles:
							if front_units < 1:
								break
							connected_tiles = get_tile_reach(tile_id, own_tiles)
							if front_tile_present_units[tile_id] >= iter:						#if a tile has more or equals the units than the iteration count, skip the tile
								continue
							for unit_idx in assignable_units:
								if front_units < 1:
									break
								if agent.units[unit_idx].location not in connected_tiles:		#based on tile occupation, see if a unit can reach this tile of the border, also prevents issues for agents with discontinuous territory
									continue
								#all checks complete, try actually moving to the tile
								agent.units[unit_idx].movetowards(tile_id, connected_tiles, enemy_locations)
								front_tile_present_units[tile_id] += 1
								front_units -= 1
								assignable_units.remove(unit_idx)

				#after moving units, consider actually using them to attack, or pushing into enemy-controlled unoccupied tiles
				enemy_troop_locations = []
				neutral_troop_locations = []
				for enemy in agent.enemies.keys():
					for location in agents[enemy].get_unit_locations():
						enemy_troop_locations.append(location)
				for other_agent in agents:
					if (other_agent.id != agent.id) and (other_agent.id not in agent.enemies.keys()):
						for location in other_agent.get_unit_locations():
							neutral_troop_locations.append(location)
				for action_tile in set(agent.get_unit_locations()):		#instead of looping through units, loop through tiles with units, as these *can* attack together
					available_units = agent.get_unit_locations().count(action_tile)
					possible_targets = []
					area_enemy_troops = 0
					for neighbor in provinces[action_tile-1].neighbors:
						if (tiles_by_controller_dict[neighbor] in agent.enemies.keys()) and (neighbor not in neutral_troop_locations):
							possible_targets.append(neighbor)
							area_enemy_troops += enemy_troop_locations.count(neighbor)
					if len(possible_targets) > 0:	#no enemy units present, spread out
						if area_enemy_troops == 0:
							target_idx = 0
							for unit in agent.units:
								if unit.location == action_tile and unit.remaining_movement > 0:
									unit.location = possible_targets[target_idx]
									event_list.append("agent {} has attacked tile {} with a unit".format(agent.id, possible_targets[target_idx]))
									unit.has_attacked = True
									target_idx +=1
									if target_idx > (len(possible_targets)-1):
										target_idx = 0
						elif available_units > area_enemy_troops:
							assignable_units = available_units
							unit_c = {}
							enemy_amounts = {}
							for neighbor in possible_targets:
								enemy_amounts[neighbor] = enemy_troop_locations.count(neighbor)

							#can only attack every tile with a numerical advantage with this many units
							units_needed_for_full_attack = 0
							for unit_placement in enemy_amounts.values():
								units_needed_for_full_attack += unit_placement + 1

							if available_units >= units_needed_for_full_attack:
								for target in possible_targets:
									unit_c[target] = enemy_troop_locations.count(target) + 1
								if available_units > units_needed_for_full_attack:
									extra_units = available_units - sum(unit_c.values())
									loop_idx = 0
									while extra_units > 0:
										tile = list(unit_c.keys())[loop_idx]
										unit_c[tile] += 1
										extra_units -= 1
										if (len(unit_c)-1) == loop_idx:
											loop_idx = 0
										else:
											loop_idx += 1
							else:
								target = max(enemy_amounts, key=enemy_amounts.get)	#attack the strongest tile only when not strongly outnumbering, attacking a weaker tile means defeat in detail becomes easier for the opposing agent
								min_assign = enemy_amounts[target] + 1
								excess_over_min = available_units - min_assign
								max_assign = sum(enemy_amounts.values())-enemy_amounts[target]

								unit_c[target] = min(max_assign, min_assign+(excess_over_min/2))
								for other_target in possible_targets:
									if other_target != target:
										unit_c[other_target] = 0
							for neighbor in possible_targets:
								if enemy_troop_locations.count(neighbor) > 0 and unit_c[neighbor] > 0:
									attack_tile(agent.id, agent.enemies.keys(), action_tile, neighbor, unit_c[neighbor])
								else:
									to_assign = unit_c[neighbor]
									for unit in agent.units:
										if to_assign == 0:
											break
										if unit.location == action_tile and unit.remaining_movement > 0:
											unit.location = neighbor
											unit.has_attacked = True
											to_assign -= 1
				for unit in agent.units:											#for unused ranged units, see if an enemy 1 tile further away can be attacked
					if unit.is_ranged() and not unit.has_attacked:
						target_tiles = {}
						for neighbor in provinces[action_tile-1].neighbors:
							for neighbor_of_neighbor in provinces[neighbor-1].neighbors:
								if tiles_by_controller_dict[neighbor_of_neighbor] in agent.enemies.keys():
									target_tiles[neighbor_of_neighbor] = enemy_troop_locations.count(neighbor_of_neighbor)
						if target_tiles != {} and any(target_tiles.values()):		#if any target tile has enemy units, pick the one with the least units for a ranged attack
							best_target = None
							least_units = None
							for location, units in target_tiles.items():
								if units > 0:
									if least_units == None or units < least_units:
										best_target = location
										least_units = units
							unit.ranged_attack(best_target, agent.enemies.keys())
		
		for agent in agents:	#peace here prevents conquered agents having their resources immediately available and prevents occupation variables from being changed mid-turn
			if agent.enemies == {} or agent.is_active == False:
				continue
			else:
				own_territory = 0
				total_territory_value = 0
				for state in states:
					if state.owner == agent.id:
						own_territory += 1
						total_territory_value += sum(state.buildings.values())
				enemies_copy = list(agent.enemies.keys())
				for enemy in enemies_copy:	#determine if territory occupied by enemy sufficiently exceeds own occupied territory, if so, cede a state and remove enemy from list #TODO add some sort of cooldown?
					conflict_duration = agent.enemies[enemy]
					if conflict_duration < 10:							#give some minimum amount of time for agents to respond
						continue
					own_warscore = 0
					enemy_warscore = 0
					enemy_controlled_states = []
					for state in states:
						if state.owner == enemy:
							if set(state.tiles) - set(agent.get_controlled_enemy_tiles()) == set():
								own_warscore += sum(state.buildings.values())
						elif state.owner == agent.id:
							if set(state.tiles) - set(agents[enemy].get_controlled_enemy_tiles()) == set():
								enemy_warscore += sum(state.buildings.values())
								enemy_controlled_states.append(state.id)
					war_should_end = False
					if enemy_warscore > total_territory_value // 2:	#victory by enemy
						war_should_end = True
					elif len(agent.get_controlled_tiles()) == 0:	#no total victory by one enemy, but no tiles left controlled on map
						war_should_end = True
					elif conflict_duration > 50:					#if discontinuous territory is completely under enemy control, cede it even if insignificant part of complete agent
						for state_own in states:
							if state.id in enemy_controlled_states:
								for state_neighbor in state_own.neighbors_state:
									if states[state_neighbor-1].owner == agent.id and state_neighbor not in enemy_controlled_states:
										break
								else:
									continue
								break								#if there is even a single break in the inner for loop, make sure the outer else below is not executed
						else:
							war_should_end = True
					if enemy_warscore > own_warscore and len(enemy_controlled_states) > 0 and war_should_end:
						for state_own in states:
							if state.id in enemy_controlled_states:
								for state_enemy in states:
									if state_enemy.id in state_own.neighbors_state and state_enemy.owner == enemy:
										state_own.owner = enemy
										break
								else:
									continue
								break			#stop outer loop if inner loop was broken, is skipped through the continue statement otherwise
						else:					#if inner loop never break'ed, assign random state
							random_state_idx = random.choice(enemy_controlled_states) - 1
							states[random_state_idx].owner = enemy
						for state in states:										#clear all occupation between agents going to peace
							if state.owner == agent.id or state.owner == enemy:
								for tile in state.tiles:
									if provinces[tile-1].controller != state.owner and (provinces[tile-1].controller == enemy or provinces[tile-1].controller == agent.id):
										provinces[tile-1].controller = None
						own_territory -= 1
						event_list.append("Agent {} ceded territory to end their war with agent {}, the war took {} turns".format(agent.id, enemy, conflict_duration))
						del agent.enemies[enemy]
						del agents[enemy].enemies[agent.id]		#note: if multiple enemies, one may get cut off, this will result in long conflict
						if own_territory == 0:
							event_list.append("Agent {} has ceased to exist".format(agent.id))
							for other_agent in agents:
								if agent.id in other_agent.enemies:
									del other_agent.enemies[agent.id]
							agent.is_active = False
							agent.calculate_reward()
							break
					elif conflict_duration > 150:	#force peace for very long stalemate, should ideally be unnecessary
						del agent.enemies[enemy]
						del agents[enemy].enemies[agent.id]
						for state in states:										#clear all occupation between agents going to peace
							if state.owner == agent.id or state.owner == enemy:
								for tile in state.tiles:
									if provinces[tile-1].controller != state.owner and (provinces[tile-1].controller == enemy or provinces[tile-1].controller == agent.id):
										provinces[tile-1].controller = None
						event_list.append("War between agent {} and {} timed out, it took {} turns".format(agent.id, enemy, conflict_duration))
				

		##display whats happening
		#Map
		if (turn_n % output_file_interval == 0 or turn_n == 1) and not disable_log:	#because the file writing is a major limiting factor otherwise, do it every x turns
			#Map - Control
			province_status = {}
			for state in states:
				for tile in state.tiles:
					province_status[tile] = state.owner
			for agent in agents:
				for tile in agent.get_controlled_enemy_tiles():
					province_status[tile] = agent.id
			#sort the dictionary, then print each row of tiles as a row in the file
			province_owners = dict(sorted(province_status.items()))
			province_rows = []
			for i in range(0, len(list(province_owners.values())), mapwidth):
				province_rows.append(list(province_owners.values())[i:i+mapwidth])
			
			preview_df = pd.DataFrame(province_rows)
			filename = "control_map_turn" + str(turn_n) + ".xlsx"

			preview_df.to_excel(filename, index = True)			#in csv, the files do not view nicely in most programs, see "import"s at top of file in case of issues

			#Map - Units
			unit_locations = {}
			total_unit_locations = []
			for agent in agents:
				total_unit_locations = total_unit_locations + agent.get_unit_locations()
			for tile in range(len(provinces)):
				if tile in total_unit_locations:
					unit_locations[tile] = total_unit_locations.count(tile)
				else:
					unit_locations[tile] = None

			unit_rows = []
			for i in range(0, len(provinces), mapwidth):
				unit_rows.append(list(unit_locations.values())[i:i+mapwidth])

			unit_df = pd.DataFrame(unit_rows)
			filename_unit = "unit_map_turn" + str(turn_n) + ".xlsx"

			unit_df.to_excel(filename_unit, index = True)


			#Map - Buildings
			province_buildings = {}
			for state in states:
				for tile in state.tiles:
					province_buildings[tile] = state.buildings['economic']+state.buildings['military']
			#sort the dictionary, then print each row of tiles as a row in the file
			province_buildings = dict(sorted(province_buildings.items()))
			province_building_rows = []
			for i in range(0, len(list(province_buildings.values())), mapwidth):
				province_building_rows.append(list(province_buildings.values())[i:i+mapwidth])
			
			building_df = pd.DataFrame(province_building_rows)
			filename_buildings = "buildings_map_turn" + str(turn_n) + ".xlsx"

			building_df.to_excel(filename_buildings, index = True)

			#Extra information to log for map
			for agent in agents:
				event_list.append("Unit locations for agent {} at time of preview file creation: {}".format(agent.id, agent.get_unit_locations()))
				if len(agent.enemies.keys()) > 0:
					event_list.append("The above agent is at war with agents "+ str(list(agent.enemies.keys()))+ " for respectively "+ str(list(agent.enemies.values()))+ " turns")
				else:
					event_list.append("The above agent has no wars")


		#Events
		if event_list != [] and not disable_log:
			with open("turn_log.txt", "a") as eventfile:
				eventfile.write("\n\nNotable actions in turn " + str(turn_n) +"\n")
				eventfile.writelines(entry + "\n" for entry in event_list)

		if turn_n >= turns_limit:
			break

	for agent in agents:
		if agent.action_type == "rfl" and agent.rfl_score == None:
			agent.calculate_reward()

	if verification_runs > 0:
		if 'prev_scores_observed' not in globals():
			prev_weights_used = []		#list of lists (values of the agent weights dictionary)
			prev_scores_observed = []	#list of lists (score list for each set of weights)
			for agent in agents:
				if agent.action_type == "rfl":
					prev_weights_used.append(list(agent.weights.values()))
					prev_scores_observed.append([agent.rfl_score])
		else:
			for agent in agents:
				if agent.action_type == "rfl":
					prev_scores_observed[prev_weights_used.index(list(agent.weights.values()))].append(agent.rfl_score)
	if run_count % change_weight_divisor == 0:
		if 'prev_weights_used' in globals():		#use the average of multiple runs if enabled
			for agent in agents:
				if agent.action_type == "rfl":
					agent.rfl_score = sum(prev_scores_observed[prev_weights_used.index(list(agent.weights.values()))])/len(prev_scores_observed[prev_weights_used.index(list(agent.weights.values()))])
			del prev_weights_used
			del prev_scores_observed
		if 'best_rfl_score' not in globals():	#otherwise compare to the previous best agent, which is still in memory, NOT to be confused with best_score
			best_rfl_score = None
			best_rfl_agent = None
		for agent in agents:
			if agent.rfl_score != None and (best_rfl_score == None or agent.rfl_score > best_rfl_score):
				best_rfl_agent = agent
				best_rfl_score = agent.rfl_score
		with open("reward_function.txt", "a") as scorefile:
			scorefile.write(str(best_rfl_score) + "\n")

	run_time = process_time() - start_time

	print("Ended simulation after turn ", turn_n, "it took", run_time, "seconds")

with open('best_weight_values.txt', 'w') as weight_file:
	for weight_v in best_rfl_agent.weights.values():
		weight_file.writelines(str(weight_v) + "\n")
