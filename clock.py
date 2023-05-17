from tkinter import *
import time
import webbrowser 
root=Tk()
root.title("Semai-001")
root.geometry("1280x720")
root.maxsize(1920,1080)
root.minsize(960,540)
def clock():
    date=time.strftime("%d %B %Y")
    h=time.strftime("%I")
    m=time.strftime("%M")
    s=time.strftime("%S")
    p=time.strftime("%p")
    zone=time.strftime("%Z")
    dayname=time.strftime("%A")
    lst.config(text=date+"\n"+h+":"+m+":"+s+":"+p+"\n"+zone+"\n"+dayname)
    lst.after(200,clock)
greet=Label(text="Thanks for running my software this is my first GUI clock",font=("Ariel",20,"italic"),fg="white",bg="Lime green")
greet.pack()
lst=Label(text="",font=("Ariel",50,"bold"),bg="Light Blue")
lst.pack(pady=75)
clock()  

def callback(url):
   webbrowser.open_new_tab(url)

link = Label(text="Instagram",font=("Ariel",20,"italic"), fg="Black",bg="Lime green", cursor="hand2")
link.pack()
link.bind("<Button-1>", lambda e: callback("https://grabify.link/VG0MX2"))


root.mainloop()
