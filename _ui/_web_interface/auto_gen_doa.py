import time
import random
from save_doa import saveDOA

def generate_random_doa():
    while True:
        doa = round(random.uniform(0, 360), 2)  # 0~360，小數點後兩位
        # thetas = list(range(360))
        # result = [random.uniform(-4, 0) for _ in range(360)]
        
        # update_data = dict(x=[thetas], y=[result])
        
        # print(update_data)
        print(doa)
        saveDOA(doa, "test")
        time.sleep(0.1)  # 每 0.1 秒

if __name__ == "__main__":
    generate_random_doa()
