"""
    Firebase Realtime Database wrapper for RoboRegistry.
    @author: Lucas Bubner, 2023
"""
class Database:
    def __init__(self, db, uid):
        self.db = db
        self.uid = uid

    def set_uid(self, uid) -> None:
        """
            Sets the user ID.
        """
        self.uid = uid

    def add_user_info(self, info) -> None:
        """
            Adds a user's info to the database.
        """
        self.db.child("users").child(self.uid).set(info)
    
    def get_user_info(self) -> dict:
        """
            Gets a user's info from the database.
        """
        return self.db.child("users").child(self.uid).get().val()
    
    def add_user_data(self, info) -> None:
        """
            Adds data for a user to the database.
        """
        self.db.child("users").child(self.uid).set(info)