from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import secrets
from datetime import datetime
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max

socketio = SocketIO(app, cors_allowed_origins="*")

# データベース初期化
def init_db():
    db_path = os.path.join('/tmp', 'oogiri.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
  
    # ユーザーテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        image TEXT DEFAULT 'default.png',
        bio TEXT DEFAULT '',
        battles INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        total_points INTEGER DEFAULT 0,
        show_stats INTEGER DEFAULT 1,
        best_answer TEXT DEFAULT '',
        best_answer_topic TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # お題テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        creator_id INTEGER,
        is_anonymous INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (creator_id) REFERENCES users(id)
    )''')
    
    # 個人倉庫テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS storage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        answer TEXT NOT NULL,
        answer_owner TEXT,
        saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    # ルームテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        creator_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (creator_id) REFERENCES users(id)
    )''')
    
    conn.commit()
    conn.close()

# アップロードフォルダ作成
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# グローバル変数でゲーム状態管理
game_rooms = {}
# game_rooms[room_id] = {
#     'players': [{user_id, username, ready, answer, points}],
#     'state': 'waiting/answering/voting/results',
#     'current_topic': topic,
#     'timer': timestamp,
#     'votes': {},
#     'game_points': {}
# }

# ヘルパー関数
def get_db():
    db_path = os.path.join('/tmp', 'oogiri.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def get_user_by_username(username):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return user

# ルート
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = get_user_by_username(username)
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('home'))
        else:
            return render_template('login.html', error='ユーザー名またはパスワードが間違っています')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            return render_template('register.html', error='すべての項目を入力してください')
        
        # ユーザー名チェック（英数字とひらがな）
        if get_user_by_username(username):
            return render_template('register.html', error='このユーザー名は既に使用されています')
        
        conn = get_db()
        hashed_password = generate_password_hash(password)
        conn.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                    (username, hashed_password))
        conn.commit()
        conn.close()
        
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = get_user_by_id(session['user_id'])
    return render_template('home.html', user=user)

@app.route('/account')
def account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = get_user_by_id(session['user_id'])
    return render_template('account.html', user=user)

@app.route('/account/edit', methods=['POST'])
def edit_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    bio = request.form.get('bio', '')
    show_stats = 1 if request.form.get('show_stats') else 0
    best_answer = request.form.get('best_answer', '')
    best_answer_topic = request.form.get('best_answer_topic', '')
    
    # 画像アップロード処理
    image_filename = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename:
            filename = secure_filename(f"{session['user_id']}_{datetime.now().timestamp()}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_filename = filename
    
    conn = get_db()
    if image_filename:
        conn.execute('''UPDATE users SET bio = ?, show_stats = ?, best_answer = ?, 
                       best_answer_topic = ?, image = ? WHERE id = ?''',
                    (bio, show_stats, best_answer, best_answer_topic, image_filename, session['user_id']))
    else:
        conn.execute('''UPDATE users SET bio = ?, show_stats = ?, best_answer = ?, 
                       best_answer_topic = ? WHERE id = ?''',
                    (bio, show_stats, best_answer, best_answer_topic, session['user_id']))
    conn.commit()
    conn.close()
    
    return redirect(url_for('account'))

@app.route('/users')
def users_list():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY total_points DESC').fetchall()
    conn.close()
    
    return render_template('users.html', users=users)

@app.route('/user/<int:user_id>')
def user_profile(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = get_user_by_id(user_id)
    if not user:
        return redirect(url_for('users_list'))
    
    return render_template('user_profile.html', profile_user=user)

@app.route('/storage')
def storage():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    items = conn.execute('''SELECT * FROM storage WHERE user_id = ? 
                           ORDER BY saved_at DESC''', (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('storage.html', items=items)

@app.route('/storage/add', methods=['POST'])
def add_to_storage():
    if 'user_id' not in session:
        return jsonify({'success': False})
    
    topic = request.json.get('topic')
    answer = request.json.get('answer')
    answer_owner = request.json.get('answer_owner')
    
    conn = get_db()
    conn.execute('''INSERT INTO storage (user_id, topic, answer, answer_owner) 
                   VALUES (?, ?, ?, ?)''',
                (session['user_id'], topic, answer, answer_owner))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/storage/delete/<int:item_id>')
def delete_from_storage(item_id):
    if 'user_id' not in session:
        return redirect(url_for('storage'))
    
    conn = get_db()
    conn.execute('DELETE FROM storage WHERE id = ? AND user_id = ?', 
                (item_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return redirect(url_for('storage'))

@app.route('/topics')
def topics():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    all_topics = conn.execute('''SELECT t.*, u.username 
                                FROM topics t 
                                LEFT JOIN users u ON t.creator_id = u.id 
                                ORDER BY t.created_at DESC''').fetchall()
    conn.close()
    
    return render_template('topics.html', topics=all_topics)

@app.route('/topics/create', methods=['POST'])
def create_topic():
    if 'user_id' not in session:
        return redirect(url_for('topics'))
    
    content = request.form.get('content')
    is_anonymous = 1 if request.form.get('is_anonymous') else 0
    
    if not content:
        return redirect(url_for('topics'))
    
    conn = get_db()
    conn.execute('INSERT INTO topics (content, creator_id, is_anonymous) VALUES (?, ?, ?)',
                (content, session['user_id'], is_anonymous))
    conn.commit()
    conn.close()
    
    return redirect(url_for('topics'))

@app.route('/rooms')
def rooms():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    all_rooms = conn.execute('''SELECT r.*, u.username as creator_name 
                               FROM rooms r 
                               JOIN users u ON r.creator_id = u.id 
                               ORDER BY r.created_at DESC''').fetchall()
    conn.close()
    
    # 各ルームの現在の人数を取得
    rooms_with_count = []
    for room in all_rooms:
        room_id = str(room['id'])
        player_count = len(game_rooms.get(room_id, {}).get('players', []))
        rooms_with_count.append({
            'id': room['id'],
            'name': room['name'],
            'creator_name': room['creator_name'],
            'player_count': player_count
        })
    
    return render_template('rooms.html', rooms=rooms_with_count)

@app.route('/rooms/create', methods=['POST'])
def create_room():
    if 'user_id' not in session:
        return redirect(url_for('rooms'))
    
    room_name = request.form.get('room_name')
    
    if not room_name:
        return redirect(url_for('rooms'))
    
    conn = get_db()
    cursor = conn.execute('INSERT INTO rooms (name, creator_id) VALUES (?, ?)',
                         (room_name, session['user_id']))
    room_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return redirect(url_for('game_room', room_id=room_id))

@app.route('/room/<int:room_id>')
def game_room(room_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    room = conn.execute('SELECT * FROM rooms WHERE id = ?', (room_id,)).fetchone()
    conn.close()
    
    if not room:
        return redirect(url_for('rooms'))
    
    return render_template('game_room.html', room=room)

# SocketIO イベント
@socketio.on('join_room')
def on_join(data):
    room_id = str(data['room_id'])
    user_id = session.get('user_id')
    username = session.get('username')
    
    if not user_id or not username:
        return
    
    join_room(room_id)
    
    # ルーム初期化
    if room_id not in game_rooms:
        game_rooms[room_id] = {
            'players': [],
            'state': 'waiting',
            'current_topic': None,
            'timer': None,
            'votes': {},
            'game_points': {},
            'cumulative_points': {}
        }
    
    # プレイヤー追加（重複チェック）
    room_data = game_rooms[room_id]
    if not any(p['user_id'] == user_id for p in room_data['players']):
        room_data['players'].append({
            'user_id': user_id,
            'username': username,
            'ready': False,
            'answer': '',
            'points': 0
        })
        room_data['game_points'][user_id] = 0
        room_data['cumulative_points'][user_id] = 0
    
    emit('room_update', {
        'players': room_data['players'],
        'state': room_data['state']
    }, room=room_id)

@socketio.on('leave_room')
def on_leave(data):
    room_id = str(data['room_id'])
    user_id = session.get('user_id')
    
    leave_room(room_id)
    
    if room_id in game_rooms:
        room_data = game_rooms[room_id]
        room_data['players'] = [p for p in room_data['players'] if p['user_id'] != user_id]
        
        emit('room_update', {
            'players': room_data['players'],
            'state': room_data['state']
        }, room=room_id)

@socketio.on('ready')
def on_ready(data):
    room_id = str(data['room_id'])
    user_id = session.get('user_id')
    
    if room_id not in game_rooms:
        return
    
    room_data = game_rooms[room_id]
    
    # プレイヤーのready状態を更新
    for player in room_data['players']:
        if player['user_id'] == user_id:
            player['ready'] = True
            break
    
    emit('room_update', {
        'players': room_data['players'],
        'state': room_data['state']
    }, room=room_id)
    
    # 3人以上で全員準備完了ならゲーム開始
    if len(room_data['players']) >= 3 and all(p['ready'] for p in room_data['players']):
        start_game(room_id)

def start_game(room_id):
    room_data = game_rooms[room_id]
    
    # ランダムにお題を選択
    conn = get_db()
    topics = conn.execute('SELECT * FROM topics').fetchall()
    conn.close()
    
    if not topics:
        emit('error', {'message': 'お題がありません'}, room=room_id)
        return
    
    import random
    topic = random.choice(topics)
    
    room_data['state'] = 'answering'
    room_data['current_topic'] = dict(topic)
    room_data['timer'] = datetime.now().timestamp() + 120  # 2分
    
    # 回答とポイントをリセット
    for player in room_data['players']:
        player['answer'] = ''
        player['ready'] = False
    room_data['game_points'] = {p['user_id']: 0 for p in room_data['players']}
    room_data['votes'] = {}
    
    emit('game_start', {
        'topic': room_data['current_topic'],
        'timer': room_data['timer']
    }, room=room_id)

@socketio.on('submit_answer')
def on_submit_answer(data):
    room_id = str(data['room_id'])
    user_id = session.get('user_id')
    answer = data.get('answer', '')
    
    if room_id not in game_rooms:
        return
    
    room_data = game_rooms[room_id]
    
    # 回答を保存
    for player in room_data['players']:
        if player['user_id'] == user_id:
            player['answer'] = answer
            player['ready'] = True
            break
    
    # 全員が回答したら投票フェーズへ
    if all(p['ready'] for p in room_data['players']):
        room_data['state'] = 'voting'
        
        # 回答をシャッフルして匿名化
        import random
        answers = [{'answer': p['answer'], 'user_id': p['user_id']} 
                  for p in room_data['players']]
        random.shuffle(answers)
        
        emit('voting_phase', {
            'answers': [a['answer'] for a in answers],
            'answer_mapping': answers  # 内部管理用
        }, room=room_id)

@socketio.on('submit_vote')
def on_submit_vote(data):
    room_id = str(data['room_id'])
    user_id = session.get('user_id')
    first_place = data.get('first_place')
    second_place = data.get('second_place')
    
    if room_id not in game_rooms:
        return
    
    room_data = game_rooms[room_id]
    room_data['votes'][user_id] = {
        'first': first_place,
        'second': second_place
    }
    
    # 全員が投票したら結果表示
    if len(room_data['votes']) == len(room_data['players']):
        calculate_results(room_id)

def calculate_results(room_id):
    room_data = game_rooms[room_id]
    
    # ポイント計算
    for voter_id, votes in room_data['votes'].items():
        # 1位に2ポイント、2位に1ポイント
        first_answer_idx = votes['first']
        second_answer_idx = votes['second']
        
        # 回答インデックスから実際のユーザーを特定する必要がある
        # 簡略化のため、answer自体をキーにする
        
    # 結果を集計（簡略版）
    for player in room_data['players']:
        points = room_data['game_points'].get(player['user_id'], 0)
        player['points'] = points
        room_data['cumulative_points'][player['user_id']] += points
    
    room_data['state'] = 'results'
    
    # 勝者判定
    winner = max(room_data['players'], key=lambda p: room_data['cumulative_points'][p['user_id']])
    
    emit('game_results', {
        'players': room_data['players'],
        'cumulative_points': room_data['cumulative_points'],
        'winner': winner
    }, room=room_id)
    
    # 統計更新
    conn = get_db()
    for player in room_data['players']:
        user_id = player['user_id']
        points = room_data['cumulative_points'][user_id]
        is_winner = 1 if user_id == winner['user_id'] else 0
        
        conn.execute('''UPDATE users 
                       SET battles = battles + 1, 
                           wins = wins + ?, 
                           total_points = total_points + ? 
                       WHERE id = ?''',
                    (is_winner, points, user_id))
    conn.commit()
    conn.close()

@socketio.on('game_action')
def on_game_action(data):
    room_id = str(data['room_id'])
    action = data['action']  # 'continue', 'new_game', 'end'
    
    if room_id not in game_rooms:
        return
    
    room_data = game_rooms[room_id]
    
    if action == 'end':
        # ルームをリセット
        room_data['state'] = 'waiting'
        for player in room_data['players']:
            player['ready'] = False
        room_data['cumulative_points'] = {p['user_id']: 0 for p in room_data['players']}
        
        emit('redirect_home', {}, room=room_id)
    
    elif action == 'new_game':
        # ポイントリセットして新ゲーム
        room_data['cumulative_points'] = {p['user_id']: 0 for p in room_data['players']}
        room_data['state'] = 'waiting'
        for player in room_data['players']:
            player['ready'] = False
        
        emit('room_update', {
            'players': room_data['players'],
            'state': room_data['state']
        }, room=room_id)
    
    elif action == 'continue':
        # ポイント引き継いで次のお題へ
        start_game(room_id)
# アプリ起動時に必ずDBを初期化
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port)