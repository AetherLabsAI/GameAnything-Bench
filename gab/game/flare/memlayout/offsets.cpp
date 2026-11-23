#include <cstddef>
#include <cstdio>
#include "CommonIncludes.h"
#include "Avatar.h"
#include "StatBlock.h"
#include "Entity.h"
#include "EntityManager.h"
#include "NPCManager.h"
#include "NPC.h"
#include "CampaignManager.h"
#include "Map.h"
#include "MapRenderer.h"
#include "MenuManager.h"
#include "Menu.h"
#include "MenuHUDLog.h"
#include "MenuTalker.h"
#include "MenuLog.h"
#include "MenuInventory.h"
#include "MenuItemStorage.h"
#include "ItemStorage.h"
#include "ItemManager.h"
#include "WidgetLog.h"
#include "EventManager.h"
#include "GameSwitcher.h"
#include "MenuRegionTitle.h"
#include "MenuBook.h"
#include "MenuVendor.h"
#include "MenuStash.h"
#include "MenuExit.h"
#include "MenuGameOver.h"
#include "MenuCharacter.h"
#include "MenuPowers.h"
#include "GameStateCutscene.h"
#define O(C,f) printf("  \"%s.%s\": %zu,\n", #C, #f, offsetof(C,f));
#define S(C) printf("  \"%s.size\": %zu,\n", #C, sizeof(C));
typedef CampaignManager::StatusMap SM;
typedef SM::value_type SMV;
int main(){
 printf("{\n");
 O(Entity,stats) O(Entity,type_filename)
 O(StatBlock,alive) O(StatBlock,corpse) O(StatBlock,hero) O(StatBlock,hero_ally) O(StatBlock,enemy_ally) O(StatBlock,npc) O(StatBlock,summoned)
 O(StatBlock,name) O(StatBlock,level) O(StatBlock,xp) O(StatBlock,character_class) O(StatBlock,character_subclass) O(StatBlock,hp) O(StatBlock,mp)
 O(StatBlock,current) O(StatBlock,pos) O(StatBlock,direction) O(StatBlock,in_combat) O(StatBlock,cur_state) O(StatBlock,permadeath)
 O(Avatar,time_played) O(Avatar,questlog_dismissed) O(Avatar,log_msg)
 O(EntityManager,entities) O(NPCManager,npcs) O(NPC,name) O(NPC,filename) O(NPC,dialog) O(NPC,talker) O(NPC,vendor)
 O(CampaignManager,status) O(CampaignManager,bonus_xp) printf("  \"SMV.first\": %zu,\n  \"SMV.second.first\": %zu,\n  \"SMV.second.second\": %zu,\n", offsetof(SMV,first), offsetof(SMV,second)+offsetof(SMV::second_type,first), offsetof(SMV,second)+offsetof(SMV::second_type,second));
 S(std::_Rb_tree_node_base)
 O(MapRenderer,filename) O(MapRenderer,title) O(MapRenderer,events) O(MapRenderer,map_npcs) O(MapRenderer,music_filename) O(MapRenderer,w) O(MapRenderer,h) O(MapRenderer,cutscene) O(MapRenderer,cutscene_file) O(MapRenderer,show_book) O(MapRenderer,loot)
 O(Event,type) O(Event,activate_type) O(Event,components) O(Event,location) O(Event,hotspot) O(Event,keep_after_trigger) S(Event)
 O(EventComponent,type) O(EventComponent,s) O(EventComponent,status) O(EventComponent,id) O(EventComponent,data) S(EventComponent)
 O(MenuManager,menus) O(MenuManager,inv) O(MenuManager,pow) O(MenuManager,chr) O(MenuManager,questlog) O(MenuManager,hudlog) O(MenuManager,book) O(MenuManager,vendor) O(MenuManager,talker) O(MenuManager,exit) O(MenuManager,stash) O(MenuManager,game_over) O(MenuManager,region_title) O(MenuManager,devconsole) O(MenuManager,pause) O(MenuManager,menus_open)
 O(Menu,visible)
 O(MenuHUDLog,log_msg) O(MenuHUDLog,msg_age) O(MenuLog,log) O(WidgetLog,messages)
 O(MenuTalker,npc) O(MenuTalker,dialog_node) O(MenuTalker,event_cursor)
 O(MenuInventory,inventory) O(MenuInventory,currency) O(MenuInventory,active_equipment_set) S(MenuItemStorage)
 O(ItemStorage,slot_number) O(ItemStorage,storage) O(ItemStack,item) O(ItemStack,quantity) S(ItemStack)
 O(ItemManager,items) O(Item,name) O(Item,quality) O(Item,type)
 O(GameSwitcher,currentState) O(GameStateCutscene,scene_index) O(GameStateCutscene,scenes) O(Scene,caption) O(MenuRegionTitle,title) O(MenuBook,book_name)
 printf("  \"_end\": 0\n}\n");
}
