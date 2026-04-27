import TkEasyGUI as eg

class BlueskyClient:
    def login(self):
        # ログインフォームの作成
        form = eg.popup_get_form(["ユーザー名", "パスワード"], title="ログイン")
        if form:
            username = form["ユーザー名"]
            password = form["パスワード"]
            
            # ユーザー名とパスワードを確認
            if self.validate_login(username, password):
                eg.print(f"ログイン成功: {username}")
            else:
                eg.print("ログイン失敗")

    def validate_login(self, username, password):
        # ここに実際のログイン認証ロジックを書く
        # 例：ユーザー名とパスワードが固定値と一致しているか確認する
        if username == "admin" and password == "password":
            return True
        else:
            return False

    def run(self):
        self.login()

if __name__ == "__main__":
    app = BlueskyClient()
    app.run()

