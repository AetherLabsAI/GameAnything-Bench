#pragma once
#include <stdint.h>
typedef uint8_t Uint8; typedef uint16_t Uint16; typedef uint32_t Uint32; typedef int32_t Sint32; typedef uint64_t Uint64; typedef int16_t Sint16;
typedef struct SDL_Color { Uint8 r, g, b, a; } SDL_Color;
typedef struct SDL_Rect { int x, y, w, h; } SDL_Rect;
typedef struct SDL_Surface SDL_Surface; typedef struct SDL_Texture SDL_Texture; typedef struct SDL_Window SDL_Window; typedef struct SDL_Renderer SDL_Renderer;
typedef struct SDL_mutex SDL_mutex; typedef struct SDL_cond SDL_cond; typedef struct SDL_Thread SDL_Thread;
typedef enum { SDL_LOG_PRIORITY_VERBOSE = 1 } SDL_LogPriority;
typedef int32_t SDL_Keycode; typedef int SDL_GameControllerButton; typedef struct _SDL_Joystick SDL_Joystick; typedef struct _SDL_GameController SDL_GameController;
typedef int32_t SDL_JoystickID;
