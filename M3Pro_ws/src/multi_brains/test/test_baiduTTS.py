import os
from ament_index_python.packages import get_package_share_directory
from multi_brains.utils.tts import BaiduTTS
import yaml
import pygame
import time
config_file=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml')
output_path=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','tts_output.wav')
print("=============================test Baidu TTS===========================")
TTS_Engine=BaiduTTS(config_file)
if TTS_Engine.init_tts_engine() :
    print("Baidu TTS_Engine initialized successfully")
else:
    print("Baidu TTS_Engine initialized failed")

res=TTS_Engine.synthesize("你好,我是一个具身智能机器人", output_path)
pygame.mixer.init()
pygame.mixer.music.load(output_path)
pygame.mixer.music.play()

while pygame.mixer.music.get_busy():
    time.sleep(0.1)