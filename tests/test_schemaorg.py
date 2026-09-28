from geo import schemaorg


def test_potential_action_input_annotations_are_valid():
    # Found on www.python.org: Google's documented sitelinks-searchbox markup.
    doc = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "url": "https://www.python.org/",
        "potentialAction": {
            "@type": "SearchAction",
            "target": "https://www.python.org/search/?q={search_term_string}",
            "query-input": "required name=search_term_string",
        },
    }
    assert schemaorg.validate(doc).ok, schemaorg.validate(doc).as_dict()


def test_invented_properties_and_types_are_errors():
    rep = schemaorg.validate({"@context": "https://schema.org", "@type": "Dentist", "name": "x",
                              "address": "1 Main", "emergencyService24x7": True})
    assert any("emergencyService24x7" in str(e) for e in rep.errors)
    assert not schemaorg.validate({"@context": "https://schema.org", "@type": "DentalSpa", "name": "x"}).ok


def test_wrong_context_and_property_on_wrong_type():
    assert not schemaorg.validate({"@context": "https://example.org", "@type": "Dentist", "name": "x", "address": "a"}).ok
    rep = schemaorg.validate({"@context": "https://schema.org", "@type": "Dentist", "name": "x", "address": "a", "isbn": "123"})
    assert any("isbn" in str(e) and "not defined" in str(e) for e in rep.errors)


def test_enumeration_values_checked():
    good = {"@context": "https://schema.org", "@type": "OpeningHoursSpecification", "dayOfWeek": "https://schema.org/Monday"}
    bad = {"@context": "https://schema.org", "@type": "OpeningHoursSpecification", "dayOfWeek": "Moonday"}
    assert schemaorg.validate(good).ok
    assert not schemaorg.validate(bad).ok
