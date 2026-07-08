
from multi_brains.utils.tts import PiperTTS
import os
import pygame
import time
pygame.mixer.init()
output_path = os.path.join(os.path.expanduser('~'),"M3Pro_ws","multi_brains_file", "test_output.wav") 
print("测试中文语音合成")
tts = PiperTTS(language="zh")  
tts.init_tts_engine()
tts.synthesize("你好,我是一个机器人", output_path)

pygame.mixer.music.load(output_path)
pygame.mixer.music.play()

while pygame.mixer.music.get_busy():
    time.sleep(0.1)

tts = PiperTTS(language="en")  
tts.init_tts_engine()
print("Test English speech synthesis")
tts.synthesize("To live or to die, this is a question worth pondering", output_path)
pygame.mixer.music.load(output_path)
pygame.mixer.music.play()

while pygame.mixer.music.get_busy():
    time.sleep(0.1)