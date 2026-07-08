from multi_brains.utils.tts import XunfeiTTS
import os
from colorama import Fore
import yaml
import pygame
import time
#-------------------------------------------config file path-------------------------------------------------
config_file=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml')
output_path =os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','XUNFEI_TTS.mp3') 
#------------------------------------------------------------------------------------------------------------

print(" test Xunfei TTS_Engine ")
with open(config_file, "r") as file:
    config_param = yaml.safe_load(file)
key_path=os.path.join(os.path.expanduser('~'),config_param.get("XUNFEI_KEY",""))
xunfei_encrypted_key=os.path.join(os.path.expanduser('~'),config_param.get("XUNFEI_ENCRYPTED",""))
tts_engine=XunfeiTTS(key_path,xunfei_encrypted_key,config_file)

if tts_engine.init_tts_engine():
    print(Fore.BLUE+"Xunfei TTS_Engine initialized successfully"+Fore.RESET)

tts_engine.synthesize("hello, I am a robot", output_path)
time.sleep(10.0)
pygame.mixer.init()
pygame.mixer.music.load(output_path)
pygame.mixer.music.play()

while pygame.mixer.music.get_busy():
    time.sleep(0.1)