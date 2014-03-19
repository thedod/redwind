from app import app

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from urllib.request import urlopen
from models import Post
from flask import jsonify, request
from flask.ext.login import login_required

import re
import requests
import views


@app.route('/api/send_webmentions', methods=['POST'])
@login_required
def send_webmentions():
    try:
        post_id = int(request.form.get('post_id'))
        post = Post.query.filter_by(id=post_id).first()
        results = mention_client.handle_new_or_edit(post)
        return jsonify(success=True, results=results)

    except Exception as e:
        app.logger.exception('sending webmentions')
        return jsonify(success=False,
                       error="exception while sending webmention: {}"
                       .format(e))


class MentionClient:
    def __init__(self):
        self.cached_responses = {}

    def get_source_url(self, post):
        return post.permalink

    def get_target_urls(self, post):
        target_urls = []

        # send mentions to 'in_reply_to' as well as all linked urls
        if post.in_reply_to:
            for in_reply_to in post.in_reply_to.strip().splitlines():
                target_urls.append(in_reply_to.strip())

        if post.repost_source:
            for repost_source in post.repost_source.strip().splitlines():
                target_urls.append(post.repost_source.strip())

        if post.like_of:
            for like_of in post.like_of.strip().splitlines():
                target_urls.append(like_of.strip())

        html_content = views.DisplayPost(post)\
                            .get_html_content(include_preview=False)

        app.logger.debug("search post content {}".format(html_content))

        soup = BeautifulSoup(html_content)
        for link in soup.find_all('a'):
            link_target = link.get('href')
            if link_target:
                app.logger.debug("found link {} with href {}"
                                 .format(link, link_target))
                target_urls.append(link_target.strip())

        return target_urls

    def get_response(self, url):
        if url in self.cached_responses:
            return self.cached_responses[url]
        response = requests.get(url)
        self.cached_responses[url] = response
        return response

    def handle_new_or_edit(self, post):
        target_urls = self.get_target_urls(post)
        app.logger.debug("Sending webmentions to these urls {}"
                         .format(" ; ".join(target_urls)))
        results = []
        for target_url in target_urls:
            results.append(self.send_mention(post, target_url))
        return results

    def send_mention(self, post, target_url):
        app.logger.debug("Looking for webmention endpoint on %s",
                         target_url)

        success, explanation = self.check_content_type_and_size(target_url)
        if success:
            if self.supports_webmention(target_url):
                app.logger.debug("Site supports webmention")
                success, explanation = self.send_webmention(post, target_url)

            elif self.supports_pingback(target_url):
                app.logger.debug("Site supports pingback")
                success, explanation = self.send_pingback(post, target_url)
                app.logger.debug("Sending pingback successful: %s", success)

            else:
                app.logger.debug("Site does not support mentions")
                success = False
                explanation = 'Site does not support webmentions or pingbacks'

        return {'target': target_url,
                'success': success,
                'explanation': explanation}

    def check_content_type_and_size(self, target_url):
        metadata = urlopen(target_url).info()
        if not metadata:
            return False, "Could not retrieve metadata for url {}".format(
                target_url)

        content_type = metadata.get_content_maintype()
        content_length = metadata.get('Content-Length')

        if content_type and content_type != 'text':
            return False, "Target content type '{}' is not 'text'".format(
                content_type)

        if content_length and int(content_length) > 2097152:
            return False, "Target content length {} is too large".format(
                content_length)

        return True, None

    def supports_webmention(self, target_url):
        return self.find_webmention_endpoint(target_url) is not None

    def find_webmention_endpoint(self, target_url):
        app.logger.debug("looking for webmention endpoint in %s", target_url)
        response = self.get_response(target_url)
        app.logger.debug("looking for webmention endpoint in headers and body")
        endpoint = (self.find_webmention_endpoint_in_headers(response.headers)
                    or self.find_webmention_endpoint_in_html(response.text))
        app.logger.debug("webmention endpoint %s %s", target_url, endpoint)
        return endpoint and urljoin(target_url, endpoint)

    def find_webmention_endpoint_in_headers(self, headers):
        if 'link' in headers:
            m = re.search('<(https?://[^>]+)>; rel="webmention"',
                          headers.get('link')) or \
                re.search('<(https?://[^>]+)>; rel="http://webmention.org/?"',
                          headers.get('link'))
            if m:
                return m.group(1)

    def find_webmention_endpoint_in_html(self, body):
        soup = BeautifulSoup(body)
        link = (soup.find('link', attrs={'rel': 'webmention'})
                or soup.find('link', attrs={'rel': 'http://webmention.org/'}))
        return link and link.get('href')

    def send_webmention(self, post, target_url):
        app.logger.debug(
            "Sending webmention from %s to %s",
            self.get_source_url(post), target_url)

        try:
            endpoint = self.find_webmention_endpoint(target_url)
            if not endpoint:
                return False, "No webmention endpoint for {}".format(
                    target_url)

            payload = {'source': self.get_source_url(post),
                       'target': target_url}
            headers = {'content-type': 'application/x-www-form-urlencoded',
                       'accept': 'application/json'}
            response = requests.post(endpoint, data=payload, headers=headers)

            #from https://github.com/vrypan/webmention-tools/blob/master/
            #             webmentiontools/send.py
            if response.status_code // 100 != 2:
                app.logger.warn(
                    "Failed to send webmention for %s. "
                    "Response status code: %s",
                    target_url, response.status_code)
                return False, "Bad response {}".format(response)
            else:
                app.logger.debug(
                    "Sent webmention successfully to %s. Sender response: %s:",
                    target_url, response.text)
                return True, "Successful"

        except Exception as e:
            return False, "Exception while sending webmention {}".format(e)

    def supports_pingback(self, target_url):
        return self.find_pingback_endpoint(target_url) is not None

    def find_pingback_endpoint(self, target_url):
        response = self.get_response(target_url)
        endpoint = response.headers.get('x-pingback')
        if not endpoint:
            soup = BeautifulSoup(response.text)
            link = soup.find('link', attrs={'rel': 'pingback'})
            endpoint = link and link.get('href')
        return endpoint

    def send_pingback(self, post, target_url):
        try:
            endpoint = self.find_pingback_endpoint(target_url)
            source_url = self.get_source_url(post)

            payload = (
                """<?xml version="1.0" encoding="iso-8859-1"?><methodCall>"""
                """<methodName>pingback.ping</methodName><params><param>"""
                """<value><string>{}</string></value></param><param><value>"""
                """<string>{}</string></value></param></params></methodCall>"""
                .format(source_url, target_url))
            headers = {'content-type': 'application/xml'}
            response = requests.post(endpoint, data=payload, headers=headers)
            app.logger.debug(
                "Pingback to %s response status code %s. Message %s",
                target_url, response.status_code, response.text)

            return True, "Sent pingback successfully"
        except Exception as e:
            return False, "Exception while sending pingback: {}".format(e)


mention_client = MentionClient()
