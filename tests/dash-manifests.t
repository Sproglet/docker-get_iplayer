use strict;
use warnings;
use Test::More;
use XML::LibXML;
use XML::LibXML::XPathContext;
use Storable qw(dclone);
use HTML::Entities qw(decode_entities);
use URI;
use HTTP::Response;
use HTTP::Request;

# Run the actual parser with offline HTTP fixtures, without executing the CLI.
my $path = shift or die 'Pass the get_iplayer source path';
open my $fh, '<', $path or die "$path: $!";
my $source = do { local $/; <$fh> };
my ($parser) = $source =~ /^(sub parse_dash_connection \{.*?^\})/ms;
die 'Cannot find DASH parser' unless $parser;
my $opt = {};
eval "no warnings;\n$parser"; # Match upstream's warning settings.
die $@ if $@;
my ($manifest, @log);
sub request_url_retry { return $manifest; }
sub logger { push @log, @_; }
{ package FixtureUA; sub request { return HTTP::Response->new(200); } }
my $ua = bless {}, 'FixtureUA';

sub parse {
    my ($attributes, $timeline) = @_;
    @log = ();
    $manifest = qq{<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT25S"><Period><AdaptationSet contentType="audio"><SegmentTemplate initialization="init-\$RepresentationID\$.mp4" media="seg-\$Number\$.m4s" $attributes>$timeline</SegmentTemplate><Representation id="audio" bandwidth="128000"/></AdaptationSet></Period></MPD>};
    return parse_dash_connection($ua, {}, {href => 'https://example.test/manifest.mpd'});
}

my @media = parse('duration="10000" timescale="1000"', '');
is(scalar @media, 1, 'Existing duration manifests still work');
is($media[0]{audio_media}{segment_duration}, 10, 'Duration uses timescale');
is($media[0]{audio_media}{num_segments}, 3, 'Partial final segment rounds count up');
@media = parse('timescale="1000"', '<SegmentTimeline><S d="10000" r="1"/><S d="5000"/></SegmentTimeline>');
is(scalar @media, 1, 'Timeline with shorter final segment works');
is($media[0]{audio_media}{segment_duration}, 10, 'Timeline uses regular segment length');
is($media[0]{audio_media}{num_segments}, 3, 'Timeline gives correct segment count');
@media = parse('timescale="1000"', '<SegmentTimeline><S d="10000" r="2"/></SegmentTimeline>');
is($media[0]{audio_media}{segment_duration}, 10, 'Single repeated timeline entry works');
for my $case (
    ['timescale="1000"', '', 'Missing duration'],
    ['duration="10000"', '', 'Missing timescale'],
    ['duration="10000" timescale="0"', '', 'Zero timescale'],
    ['timescale="1000"', '<SegmentTimeline><S d="10000"/><S d="9000"/><S d="6000"/></SegmentTimeline>', 'Variable timeline'],
) {
    my @result = eval { parse($case->[0], $case->[1]) };
    is($@, '', "$case->[2] does not crash");
    is(scalar @result, 0, "$case->[2] skipped");
    like(join('', @log), qr/Cannot determine DASH segment duration/, "$case->[2] warns");
}
done_testing;
