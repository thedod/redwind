from app import app
from flask import redirect, url_for, abort
import base60
from datetime import date

TAG_TO_TYPE = {
    'n': 'note',
    'a': 'article',
    'r': 'reply'}

START_ORDINAL = date(1970, 1, 1).toordinal()


@app.route('/short/<string(minlength=5,maxlength=6):tag>')
def shortlink(tag):
    type_enc = tag[0]
    date_enc = tag[1:4]
    index_enc = tag[4:]

    post_type = TAG_TO_TYPE.get(type_enc)
    ordinal = base60.decode(date_enc)
    index = base60.decode(index_enc)

    if not post_type or not ordinal or not index:
        abort(404)

    pub_date = date.fromordinal(START_ORDINAL + ordinal)
    return redirect(url_for('post_by_date', post_type=post_type,
                            year=pub_date.year, month=pub_date.month,
                            day=pub_date.day, index=index))