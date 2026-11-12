"""Reviewed original NPC scene storage layouts; never inferred during export."""

SCENE_WORLD_FIELDS = (
    '_ai_link_revision',
    '_ai_linked_targets',
    '_array_filter_revisions',
    '_native_state_misses',
    '_owned_deployables',
    '_possession_revisions',
    'absent_spatial_binding_owners',
    'ai_behavior_requests',
    'ai_control_records',
    'ai_control_settings',
    'ai_difficulties',
    'ai_facing_records',
    'ai_weapon_records',
    'ai_weapon_slots',
    'alias_components',
    'array_filter_inputs',
    'b61_registries',
    'combat_damage_filters',
    'component44_body_ids',
    'component45_spatial_contexts',
    'component76_records',
    'component86_registries',
    'contact_effect',
    'control_contexts',
    'controller_records',
    'corpse_retirements',
    'current_possessions',
    'data_flow_contexts',
    'definitions',
    'display_name_overrides',
    'effect_contexts',
    'effect_entity_action',
    'effect_targets',
    'enemy_participant_roster',
    'engine_settings',
    'entities',
    'entity_creator',
    'entity_directions',
    'entity_filter_records',
    'entity_start',
    'explosion_damage',
    'ghost_contact_damage',
    'ghost_targets',
    'global_data_flow',
    'health_pool_amounts',
    'health_state_counters',
    'hero_contexts',
    'hero_creator',
    'indexed_targets',
    'junkenstein_body_name',
    'junkenstein_voice_recipients',
    'linked_controllers',
    'look_contexts',
    'map_guid',
    'message_contexts',
    'mission_message_route',
    'mode_score_thresholds',
    'model_gate_consumers',
    'modifier_components',
    'objective_route',
    'participant_roster',
    'placements',
    'play_queues',
    'player_participant_roster',
    'possessors',
    'primary_possessors',
    'query_contexts',
    'references',
    'roadhog_hook_cast',
    'roadhog_hook_hit',
    'roadhog_hook_target',
    'roadhog_hook_targets',
    'scope_components',
    'script_components',
    'script_team_setter',
    'session_entity_roster',
    'shield_recharge',
    'spawn_search',
    'symmetra_beam_cast',
    'symmetra_beam_hit',
    'target_record_scalars',
    'target_tag_grants',
    'target_tag_members',
    'team_contexts',
    'team_member_count',
    'update_vengeful_ghost',
    'voice_category_callback',
    'voice_contexts',
    'voice_set_overrides',
    'voice_sets',
    'wave_health',
    'witch_staff_heal',
    'witch_staff_targets',
)

SCENE_ENTITY_FIELDS = (
    'id',
    'reference',
    'position',
    'placement',
    'definition',
    'target_tags',
    'health',
    'health_pool',
    'hero',
    'pool_capacity',
    'velocity',
    'position_reader',
    'self_damage',
    'pet_component_absent',
    'normal_health',
    'component92_present',
    'movement_reader',
    'scale_reader',
    'on_fire_reader',
    'same_team_contact',
    'maximum_health',
    'rotation_reader',
    'facing_writer',
    'aim_writer',
)

PLACEMENT_FIELDS = (
    'index',
    'flat',
    'client_id',
    'reference',
    'definition',
    'identifier',
    'position',
    'source',
)


def is_scene_revision(value):
    """Recognize the three original SceneWorld dependency namespaces."""
    from ow174.game.script import runtime
    if (type(value) is not runtime.Var or value.entity_owner is not None
            or runtime.Var.__slots__ != ('base', 'key', 'links', 'partial', 'resolving', 'watchers', 'entity_owner')):
        return False
    key = value.key
    if type(key) is not tuple or len(key) != 2 or type(key[0]) is not str:
        return False
    return ((key[0] == 'scene' and type(key[1]) is str and key[1] == 'AI linked targets')
            or (key[0] in ('possession', 'array filter') and type(key[1]) is int))


def phase2_observer_codes():
    """Only the original empty observer in prepare_scene is a stored callback."""
    import types
    from ow174.game.assassin_controller import Phase2World
    return tuple(code for code in Phase2World.prepare_scene.__code__.co_consts
                 if type(code) is types.CodeType and code.co_name == '<lambda>')
