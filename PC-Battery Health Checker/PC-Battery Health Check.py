# BAttery percentage check
# Run cmd as Administrator and type(powercfg/Batteryreport)
x=int(input("Your last full charge capacity: "))
y=int(input("Your original battery build capacity:"))
if x > y  :
    print("\n","-------OUTPUT-------")
    print("Invalid input !! \nBattery current % can not be more than original capacity ! ")
elif x==y:
    print("\n","-------OUTPUT-------")
    print("Your battery is all ok and can be charged to 100% at full charge.")
else:
    z=(x/y)*100
    print("\n","-------OUTPUT-------")
    print(f"Your battery Can now be charged to --> {z}%  <-- at (100%) charge capacity.")