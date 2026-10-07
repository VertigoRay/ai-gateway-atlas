# Build-only renderer. This image is never a web server or a broker runtime.
FROM ruby:3.3-alpine@sha256:d9e5298c8c7dad54b40facac94be1baa10f593db7dec7a9afb47bd44a64c15a9
RUN apk add --no-cache build-base git && gem install bundler -v 4.0.22 --no-document
WORKDIR /opt/reference
COPY Gemfile Gemfile.lock ./
RUN bundle config set --local frozen true && bundle install --jobs 4 --retry 3
ENV BUNDLE_GEMFILE=/opt/reference/Gemfile JEKYLL_ENV=production
ENTRYPOINT ["bundle", "exec"]
CMD ["jekyll", "build", "--safe", "--disable-disk-cache"]
