# Used for cleaning up various parts of CODA. Mainly, when the program updates or when uninstalling.
import sys


def clean_up_after_update():
    print("Starting cleanup...")
    # Delete the old version
    print("Deleting the old version...")
    try:
        import shutil
        shutil.rmtree("coda-old-version")
    except Exception as e:
        print(f"Error deleting the old version: {e}")
    
    pass
        
def uninstall():
    print ("NOT IMPLEMENTED YET...")
    pass

if __name__ == "__main__":
    if "-uninstall" in sys.argv:
        uninstall()
    elif "-update" in sys.argv:
        clean_up_after_update()