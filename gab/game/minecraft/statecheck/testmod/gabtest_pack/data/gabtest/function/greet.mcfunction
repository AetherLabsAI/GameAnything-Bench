tag @s add gab_seen
tag @s add gab_visited_shrine
scoreboard objectives add gab_quest dummy "GAB QUEST"
scoreboard objectives setdisplay sidebar gab_quest
scoreboard players set stage gab_quest 2
data modify storage gabtest:state stage set value "stage_two"
summon minecraft:villager ~3 ~ ~ {CustomName:"Beacon Keeper",NoAI:1b,Invulnerable:1b,Tags:["gab_beacon"],PersistenceRequired:1b}
bossbar add gabtest:bar "GAB TEST BAR"
bossbar set gabtest:bar players @a
title @s times 10 2400 10
title @s title "GAB TEST TITLE"
tellraw @s "the beacon is near"
