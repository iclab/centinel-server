import centinel
import centinel.models
import centinel.views
import config
import os


if __name__ == "__main__":
    db = centinel.db
    app = centinel.app

    sql_dir = os.path.dirname(config.sqlite_db)
    if not os.path.exists(sql_dir):
        os.makedirs(sql_dir)
    if not os.path.exists(config.sqlite_db):
        db.create_all()
        # create an admin and client role
        db.session.add(centinel.models.Role('admin'))
        db.session.add(centinel.models.Role('client'))
        db.session.commit()

    # Also, I shouldn't have to say this, but *DO NOT COMMIT THE
    # KEY*. Under no circumstances should the key be committed
    app.run(host="0.0.0.0", port=8082, debug=True,
            ssl_context=(config.ssl_cert, config.ssl_key))
